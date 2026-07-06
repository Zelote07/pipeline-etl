#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de Scraping Web pour Letterboxd.
Ce script lit les films (uniquement de type "Movie") des catalogues locaux,
recherche chaque film sur Letterboxd via une stratégie hybride (liaison TMDB/IMDb, puis slugification),
puis extrait les données clés :
- Note moyenne de la communauté
- Nombre de votes
- Nombre de fans (likes)
- Identifiants de liaison (IMDb / TMDB si présents dans les métadonnées de Letterboxd)
Les données sont sauvegardées de façon incrémentale dans data/raw/letterboxd_raw_responses.json.
"""

import os
import json
import time
import re
import random
import argparse
import urllib.parse
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Liste de User-Agents pour éviter les blocages
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/114.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/114.0.1823.67"
]

def slugify(text):
    """
    Transforme un titre en slug propre compatible avec Letterboxd.
    Ex: "Toy Story 2" -> "toy-story-2"
    """
    text = str(text).lower().strip()
    # Remplacer les caractères accentués courants
    replacements = {
        'à': 'a', 'á': 'a', 'â': 'a', 'ã': 'a', 'ä': 'a', 'å': 'a',
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'í': 'i', 'ì': 'i', 'î': 'i', 'ï': 'i',
        'ó': 'o', 'ò': 'o', 'ô': 'o', 'õ': 'o', 'ö': 'o',
        'ú': 'u', 'ù': 'u', 'û': 'u', 'ü': 'u',
        'ç': 'c', 'ñ': 'n'
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text)
    return text.strip('-')

def load_unique_movies(data_dir):
    """
    Charge les fichiers CSV bruts de Netflix, Disney+ et Amazon Prime.
    Filtre pour ne garder que les films ("Movie") et renvoie les titres uniques.
    """
    files = {
        "netflix": os.path.join(data_dir, "netflix_titles.csv"),
        "disney": os.path.join(data_dir, "disney_plus_titles.csv"),
        "amazon": os.path.join(data_dir, "amazon_prime_titles.csv")
    }

    dfs = []
    for platform, path in files.items():
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, usecols=["type", "title", "release_year"])
                df["platform"] = platform
                dfs.append(df)
            except Exception as e:
                print(f"[ERROR] Impossible de charger {path} : {e}")

    if not dfs:
        print("[ERROR] Aucun fichier CSV trouvé dans data/raw/. Arrêt du script.")
        return []

    # Fusionner
    combined_df = pd.concat(dfs, ignore_index=True)
    
    # Filtrer uniquement les films ("Movie")
    movies_df = combined_df[combined_df["type"].str.lower().str.contains("movie", na=False)].copy()
    
    # Nettoyer et dédupliquer
    movies_df = movies_df.dropna(subset=["title"])
    unique_movies = movies_df.drop_duplicates(subset=["title", "release_year"]).copy()
    
    print(f"[INFO] Total films chargés de toutes les plateformes : {len(movies_df)}")
    print(f"[INFO] Films uniques à traiter : {len(unique_movies)}")
    
    return unique_movies.to_dict(orient="records")

def make_cache_key(title, release_year):
    """
    Génère une clé de cache normalisée pour Letterboxd.
    """
    t_clean = str(title).strip().lower()
    year_clean = str(int(release_year)) if pd.notna(release_year) else "unknown"
    return f"movie:{t_clean}:{year_clean}"

def load_cache(cache_path):
    """
    Charge le cache existant des réponses Letterboxd.
    """
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            print(f"[INFO] Cache Letterboxd chargé avec {len(cache)} entrées depuis {cache_path}")
            return cache
        except Exception as e:
            print(f"[ERROR] Impossible de charger le cache {cache_path} : {e}. Création d'un nouveau.")
    return {}

def save_cache(cache, cache_path):
    """
    Sauvegarde le cache de données brutes.
    """
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Cache Letterboxd sauvegardé ({len(cache)} entrées) dans {cache_path}")
    except Exception as e:
        print(f"[ERROR] Échec de la sauvegarde du cache : {e}")

def get_headers():
    """
    Retourne des en-têtes HTTP réalistes avec un User-Agent aléatoire.
    """
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://letterboxd.com/",
        "Connection": "keep-alive"
    }

def find_tmdb_id_from_cache(title, release_year, tmdb_cache):
    """
    Recherche si le film possède un ID TMDB dans le cache d'extraction TMDB local.
    """
    if not tmdb_cache:
        return None
        
    cache_key = f"movie:{str(title).strip().lower()}:{str(int(release_year)) if pd.notna(release_year) else 'unknown'}"
    entry = tmdb_cache.get(cache_key)
    if entry and isinstance(entry, dict):
        details = entry.get("details")
        search_res = entry.get("search_result")
        
        tmdb_id = None
        if isinstance(details, dict):
            tmdb_id = details.get("id")
        if not tmdb_id and isinstance(search_res, dict):
            tmdb_id = search_res.get("id")
            
        if tmdb_id:
            return tmdb_id
    return None

def parse_movie_page(movie_url, session):
    """
    Télécharge la page de détails du film sur Letterboxd et en extrait les métadonnées clés.
    """
    try:
        response = session.get(movie_url, headers=get_headers(), timeout=15)
        if response.status_code == 404:
            return {"status": 404}
        if response.status_code == 429:
            print("[WARNING] Code 429 (Rate Limit) détecté. Pause de 10s...")
            time.sleep(10)
            return {"status": 429}
        if response.status_code != 200:
            print(f"[WARNING] Impossible d'accéder à la page {movie_url} (Code {response.status_code})")
            return None
            
        resolved_url = response.url
        soup = BeautifulSoup(response.text, "lxml")
        
        # 1. Extraction via JSON-LD
        rating_value = None
        rating_count = None
        imdb_id = None
        tmdb_id = None
        
        json_ld_tags = soup.find_all("script", type="application/ld+json")
        for tag in json_ld_tags:
            try:
                # Nettoyer CDATA
                clean_json = tag.string
                clean_json = re.sub(r'/\*\s*<!\[CDATA\[\s*\*/', '', clean_json)
                clean_json = re.sub(r'/\*\s*\]\]>\s*\*/', '', clean_json).strip()
                
                data = json.loads(clean_json)
                
                if data.get("@type") == "Movie" or "Movie" in data.get("@type", []):
                    agg_rating = data.get("aggregateRating")
                    if agg_rating:
                        rating_value = agg_rating.get("ratingValue")
                        rating_count = agg_rating.get("ratingCount")
                    
                    same_as = data.get("sameAs", [])
                    if isinstance(same_as, str):
                        same_as = [same_as]
                    for link in same_as:
                        if "imdb.com/title" in link:
                            imdb_id = link.rstrip("/").split("/")[-1]
                        elif "themoviedb.org/movie" in link:
                            try:
                                tmdb_id = int(link.rstrip("/").split("/")[-1])
                            except ValueError:
                                pass
                    break
            except Exception:
                continue

        # 2. Liaison alternative via les liens HTML (si non présent ou incomplet dans le JSON-LD)
        if not imdb_id:
            imdb_tag = soup.find("a", href=lambda h: h and "imdb.com/title/" in h)
            if imdb_tag:
                href = imdb_tag.get("href")
                match = re.search(r'tt\d+', href)
                if match:
                    imdb_id = match.group(0)

        if not tmdb_id:
            tmdb_tag = soup.find("a", href=lambda h: h and "themoviedb.org/movie/" in h)
            if tmdb_tag:
                href = tmdb_tag.get("href")
                match = re.search(r'movie/(\d+)', href)
                if match:
                    tmdb_id = int(match.group(1))

        # 3. Extraction du nombre de Fans (likes de Letterboxd) via le lien /fans/
        fans_count = None
        fans_tag = soup.find("a", href=lambda h: h and h.endswith("/fans/"))
        if fans_tag:
            fans_text = fans_tag.get_text().strip().lower()
            fans_text = fans_text.replace("fans", "").strip()
            try:
                if "k" in fans_text:
                    fans_count = int(float(fans_text.replace("k", "")) * 1000)
                elif "m" in fans_text:
                    fans_count = int(float(fans_text.replace("m", "")) * 1000000)
                else:
                    # Gérer les espaces insécables et virgules
                    fans_text_clean = fans_text.replace("\xa0", "").replace(",", "").replace(" ", "").strip()
                    fans_count = int(fans_text_clean)
            except ValueError:
                pass
        
        return {
            "status": 200,
            "movie_url": resolved_url,
            "rating_value": rating_value,
            "rating_count": rating_count,
            "likes_count": fans_count,  # Nombre de fans (équivalent aux likes du film)
            "imdb_id": imdb_id,
            "tmdb_id": tmdb_id,
            "found": True
        }
        
    except Exception as e:
        print(f"[ERROR] Erreur lors du scraping de {movie_url} : {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Web Scraper Letterboxd pour les films.")
    parser.add_argument("--limit", type=int, default=5, help="Nombre max de films à scraper pour cette session (par défaut : 5).")
    parser.add_argument("--all", action="store_true", help="Scraper tous les films restants sans limite.")
    parser.add_argument("--save-every", type=int, default=10, help="Fréquence de sauvegarde du cache (par défaut : toutes les 10 requêtes).")
    parser.add_argument("--min-delay", type=float, default=1.5, help="Délai minimal entre les requêtes (par défaut : 1.5s).")
    parser.add_argument("--max-delay", type=float, default=3.5, help="Délai maximal entre les requêtes (par défaut : 3.5s).")
    
    args = parser.parse_args()

    # Déterminer les dossiers
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raw_data_dir = os.path.join(base_dir, "data", "raw")
    cache_file_path = os.path.join(raw_data_dir, "letterboxd_raw_responses.json")
    tmdb_cache_path = os.path.join(raw_data_dir, "tmdb_raw_responses.json")

    print("[INFO] Démarrage du Web Scraper Letterboxd...")

    # 1. Charger les films uniques
    movies_to_process = load_unique_movies(raw_data_dir)
    if not movies_to_process:
        return

    # 2. Charger les caches existants
    cache = load_cache(cache_file_path)
    
    tmdb_cache = {}
    if os.path.exists(tmdb_cache_path):
        try:
            with open(tmdb_cache_path, "r", encoding="utf-8") as f:
                tmdb_cache = json.load(f)
            print(f"[INFO] Cache TMDB local chargé pour résolution d'IDs ({len(tmdb_cache)} entrées)")
        except Exception as e:
            print(f"[WARNING] Impossible de charger le cache TMDB pour la liaison : {e}")

    # Créer une session HTTP réutilisable
    session = requests.Session()

    # 3. Scraping des films
    scraped_count = 0
    newly_saved_count = 0
    skipped_count = 0
    not_found_count = 0

    max_scrapes = float('inf') if args.all else args.limit

    print(f"[INFO] Traitement en cours (Limite max de scraping : {'Illimitée' if args.all else args.limit})...")

    try:
        for idx, item in enumerate(movies_to_process):
            title = item["title"]
            release_year = item["release_year"]
            
            cache_key = make_cache_key(title, release_year)
            
            # Vérifier si c'est déjà dans le cache Letterboxd
            # Si le film est en cache mais que nous voulons retenter les "non trouvés"
            # (parce qu'on a modifié l'algorithme de recherche), nous pouvons supprimer
            # la condition "not cache[cache_key].get('found')" ou écraser les échecs passés.
            # Pour ce run, nous allons écraser les entrées qui ont "found": False pour
            # tester l'efficacité de la nouvelle version.
            if cache_key in cache:
                if cache[cache_key].get("found", False):
                    skipped_count += 1
                    continue
                else:
                    # Retenter les "Non trouvés" de la session précédente ratée due au 403
                    pass
                
            # Limite atteinte
            if scraped_count >= max_scrapes:
                print(f"[INFO] Limite de scraping de {args.limit} atteinte pour cette session.")
                break

            print(f"[{scraped_count+1}/{max_scrapes}] Scraping Letterboxd pour : {title} ({release_year})")
            
            # Algorithme de recherche hybride résilient
            tmdb_id = find_tmdb_id_from_cache(title, release_year, tmdb_cache)
            urls_to_try = []

            # Stratégie 1 : Route TMDB (La plus propre)
            if tmdb_id:
                urls_to_try.append((f"https://letterboxd.com/tmdb/{tmdb_id}/", "TMDB ID Route"))
            
            # Stratégie 2 : Slug direct de base
            base_slug = slugify(title)
            if base_slug:
                urls_to_try.append((f"https://letterboxd.com/film/{base_slug}/", "Direct Slug Route"))
                
                # Stratégie 3 : Slug + Année
                if pd.notna(release_year):
                    urls_to_try.append((f"https://letterboxd.com/film/{base_slug}-{int(release_year)}/", "Slug-Year Route"))

            # Tenter les URLs une par une
            movie_data = None
            for url, strategy_name in urls_to_try:
                delay = random.uniform(args.min_delay, args.max_delay)
                time.sleep(delay)
                
                print(f"  -> Tentative via {strategy_name} : {url}")
                result = parse_movie_page(url, session)
                
                if result:
                    if result.get("status") == 200:
                        movie_data = result
                        print(f"    -> Succès ! Film résolu : {movie_data['movie_url']}")
                        break
                    elif result.get("status") == 429:
                        # Rate limit, on s'arrête là pour ce film pour ne pas spammer
                        break
                    elif result.get("status") == 404:
                        # 404, on passe à la stratégie suivante
                        continue
            
            # Si le film est résolu
            if movie_data:
                cache[cache_key] = movie_data
                newly_saved_count += 1
                scraped_count += 1
                print(f"    -> Données extraites : Note={movie_data['rating_value']}/5, Votes={movie_data['rating_count']}, Fans/Likes={movie_data['likes_count']}, TMDB ID={movie_data['tmdb_id']}, IMDb ID={movie_data['imdb_id']}")
            else:
                # Enregistrer comme "Non trouvé" pour éviter d'y revenir
                cache[cache_key] = {
                    "movie_url": None,
                    "rating_value": None,
                    "rating_count": None,
                    "likes_count": None,
                    "imdb_id": None,
                    "tmdb_id": tmdb_id,
                    "found": False
                }
                newly_saved_count += 1
                scraped_count += 1
                not_found_count += 1
                print("  -> Impossible de trouver le film sur Letterboxd après toutes les stratégies.")

            # Sauvegarde régulière du cache
            if scraped_count > 0 and scraped_count % args.save_every == 0:
                save_cache(cache, cache_file_path)

    except KeyboardInterrupt:
        print("\n[WARNING] Interruption par l'utilisateur. Sauvegarde du cache...")
    finally:
        if newly_saved_count > 0:
            save_cache(cache, cache_file_path)

        print("\n=== Bilan du Scraping Letterboxd ===")
        print(f"Films déjà en cache (ignorés) : {skipped_count}")
        print(f"Nouveaux films traités (incl. échecs passés retentés) : {scraped_count}")
        print(f"Nouveaux films enregistrés : {newly_saved_count}")
        print(f"Films non trouvés (total historique) : {not_found_count}")
        print(f"Total des films dans le cache final : {len(cache)}")
        print("====================================")

if __name__ == "__main__":
    main()
