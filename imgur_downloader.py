import requests
from bs4 import BeautifulSoup
import os
import sys
import argparse
import re
import glob
from urllib.parse import urlparse

def download_file(url, folder):
    """
    Pobiera plik z danego URL i zapisuje go w folderze.
    Zwraca nazwę zapisanego pliku (filename) jeśli sukces, w przeciwnym razie None.
    """
    # Imgur często blokuje requesty bez odpowiedniego User-Agent i Referer (zwraca 429)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://imgur.com/'
    }
    
    try:
        response = requests.get(url, stream=True, headers=headers)
        response.raise_for_status()
        
        # Wyciągnij nazwę pliku z URL
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        
        if not filename or '.' not in filename:
            # Fallback jeśli brak nazwy w URL (np. przekierowanie)
            filename = "downloaded_image.jpg"

        filepath = os.path.join(folder, filename)
        
        # Unikaj nadpisywania plików (jeśli plik już istnieje, dodaj licznik)
        # UWAGA: W trybie replace, jeśli mamy ten sam URL to chcemy dostać ten sam plik,
        # ale funkcja 'download_file' jest generyczna.
        # Logikę deduplikacji URL -> Plik obsłużymy wyżej.
        # Tutaj tylko dbamy o kolizje nazw plików.
        
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(filepath):
            # Sprawdźmy czy to może ten sam plik (w uproszczeniu: długość)
            # Ale bezpieczniej po prostu zapisać nowy unikając nadpisania
            filepath = os.path.join(folder, f"{base}_{counter}{ext}")
            counter += 1
            filename = os.path.basename(filepath)

        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print(f"[OK] Pobrano: {filepath}")
        return filename
    except Exception as e:
        print(f"[BŁĄD] Nie udało się pobrać {url}: {e}")
        return None

def get_images_from_gallery(url):
    """Próbuje znaleźć linki do obrazów na stronie Imgur (album/galeria)."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        image_urls = []

        # Metoda 1: Szukanie tagów meta (og:image)
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            url = og_image["content"]
            if '?' in url:
                url = url.split('?')[0]
            image_urls.append(url)

        # Metoda 2: Kontenery
        post_images = soup.find_all("div", class_="post-image-container")
        for div in post_images:
            img_div = div.find("div", class_="post-image")
            if img_div:
                img_tag = img_div.find("img")
                if img_tag and img_tag.get("src"):
                     src = "https:" + img_tag["src"] if img_tag["src"].startswith("//") else img_tag["src"]
                     image_urls.append(src)
                
                meta_tag = img_div.find("meta", itemprop="contentUrl")
                if meta_tag and meta_tag.get("content"):
                    image_urls.append(meta_tag["content"])

        if not image_urls:
            link_rel_image = soup.find("link", rel="image_src")
            if link_rel_image and link_rel_image.get("href"):
                 image_urls.append(link_rel_image["href"])

        return list(set(image_urls))

    except Exception as e:
        print(f"[BŁĄD] Nie udało się przetworzyć galerii: {e}")
        return []

def process_file_replacement(file_pattern, output_dir, web_prefix):
    """Skanuje pliki, pobiera obrazy i podmienia linki."""
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    # Znajdź wszystkie pliki pasujące do wzorca (np. *.html)
    files = glob.glob(file_pattern)
    if not files:
        print(f"Nie znaleziono plików pasujących do wzorca: {file_pattern}")
        return

    # Cache: URL -> nazwa_lokalna (bez prefixu)
    # Żeby nie pobierać tego samego obrazka wielokrotnie dla różnych plików lub w tym samym pliku
    url_map = {}

    imgur_regex = re.compile(r'(https?://(?:i\.)?imgur\.com/[a-zA-Z0-9]+\.(?:jpg|jpeg|png|gif|mp4))', re.IGNORECASE)

    for filepath in files:
        print(f"Przetwarzanie pliku: {filepath}...")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Znajdź wszystkie linki
            links = imgur_regex.findall(content)
            unique_links = set(links)
            
            if not unique_links:
                print(" -> Brak linków Imgur.")
                continue
                
            print(f" -> Znaleziono {len(unique_links)} unikalnych linków.")
            
            # Pobieranie
            replacements = {} # stary_link -> nowy_link_z_prefixem
            
            for link in unique_links:
                if link in url_map:
                    filename = url_map[link]
                    print(f" -> [Cache] Używam już pobranego: {filename}")
                else:
                    print(f" -> Pobieranie: {link}")
                    filename = download_file(link, output_dir)
                    if filename:
                        url_map[link] = filename
                    else:
                        print(f" -> [SKIP] Nie udało się pobrać {link}, pomijam podmianę.")
                        continue
                
                # Budowanie nowej ścieżki do wstawienia
                if web_prefix:
                    # Łączenie prefixu z nazwą pliku. 
                    # Uważamy na slashe, żeby nie dublować (np assets/ + /img.jpg)
                    clean_prefix = web_prefix.rstrip('/')
                    if clean_prefix:
                         new_path = f"{clean_prefix}/{filename}"
                    else:
                         new_path = filename
                else:
                    new_path = filename
                
                replacements[link] = new_path

            # Podmiana w treści
            new_content = content
            count = 0
            for link, new_path in replacements.items():
                # Używamy replace stringa, bo regex.findall dał nam dokładne stringi
                # Ale uwaga: jeśli jeden link jest podciągiem drugiego (mało prawdobodobne w imgur direct links)
                new_content = new_content.replace(link, new_path)
                count += 1
            
            # Zapisz zmieniony plik
            if new_content != content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f" -> Zaktualizowano plik {filepath} (podmieniono {count} wystąpień).")
            else:
                 print(" -> Brak zmian w pliku.")

        except Exception as e:
            print(f"[BŁĄD] Problem z plikiem {filepath}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Imgur Downloader & Link Replacer")
    
    # Tryby pracy:
    # 1. URL (stary tryb)
    # 2. --replace (nowy tryb)
    
    # Ponieważ URL był pozycyjny, zrobimy go opcjonalnym, ale zrobimy grupę wykluczającą
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('url', nargs='?', help="Link do Imgur (pojedynczy lub album)")
    group.add_argument('--replace', help="Ścieżka/wzorzec do plików, w których podmienić linki (np. index.html, *.css)")

    parser.add_argument('folder', nargs='?', default='downloads', help="Folder docelowy (tylko w trybie URL, domyślnie 'downloads')")
    parser.add_argument('--output-dir', help="Folder docelowy dla trybu --replace")
    parser.add_argument('--web-prefix', help="Prefix dodawany do ścieżki w pliku (np. 'assets/'). Domyślnie brak (sama nazwa pliku).")

    args = parser.parse_args()

    if args.replace:
        # Tryb Replace
        if not args.output_dir:
            print("Wymagany argument --output-dir w trybie --replace (np. c:/strona/assets)")
            return
        
        # web_prefix - jeśli nie podany, to puste (sama nazwa pliku)
        prefix = args.web_prefix if args.web_prefix else ""
        
        process_file_replacement(args.replace, args.output_dir, prefix)

    elif args.url:
        # Tryb klasyczny
        url = args.url
        # Jeśli użyto flagi --output-dir, ma pierwszeństwo przed argumentem pozycyjnym
        output_folder = args.output_dir if args.output_dir else args.folder
        
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        print(f"Rozpoczynam pracę dla URL: {url}")
        print(f"Folder docelowy: {os.path.abspath(output_folder)}")

        parsed = urlparse(url)
        path = parsed.path.lower()
        
        direct_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.mp4']
        if any(path.endswith(ext) for ext in direct_extensions):
            print("Wykryto bezpośredni link do pliku.")
            download_file(url, output_folder)
        else:
            print("Wykryto link do strony/galerii. Próba znalezienia obrazów...")
            images = get_images_from_gallery(url)
            if images:
                print(f"Znaleziono {len(images)} potencjalnych obrazów.")
                for img_url in images:
                    download_file(img_url, output_folder)
            else:
                print("Nie znaleziono obrazów.")
                # Fallback ID
                if '/' in path and '.' not in path:
                    possible_id = path.strip('/').split('/')[-1]
                    if possible_id:
                        print(f"Próba zgadywania ID: {possible_id}")
                        guess_url = f"https://i.imgur.com/{possible_id}.jpg"
                        download_file(guess_url, output_folder)

if __name__ == "__main__":
    main()
