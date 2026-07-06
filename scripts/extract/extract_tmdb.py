#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de collecte (Extraction) des données TMDB.
Ce script lit les catalogues bruts (Netflix, Disney+, Amazon Prime), en extrait les titres uniques,
puis interroge l'API de TMDB (The Movie Database) pour récupérer des informations complémentaires
(notes, popularité, budget, revenus, genres, runtime, etc.).
Les réponses brutes sont sauvegardées de façon incrémentale dans data/raw/tmdb_raw_responses.json.
"""

import os
import json
import time
import argparse
import pandas as pd
import requests
from dotenv import load_dotenv

# Charger les variables d'environnement (.env)
load_dotenv()

# URLs de base de l'API TMDB
TMDB_BASE_URL = "https://api.themoviedb.org/3"

def get_auth_config():
    """
    Configure l'authentification pour TMDB en lisant le fichier .env.
    Retourne un tuple (headers, params).
    Priorise le Bearer Token (TMDB_READ_TOKEN) puis la clé API standard (TMDB_API_KEY).
    """
    read_token = os.getenv("TMDB_READ_TOKEN")
    api_key = os.getenv("TMDB_API_KEY")

    headers = {}
    params = {}

    if read_token and read_token != "your_tmdb_read_token_here":
        headers["Authorization"] = f"Bearer {read_token}"
        headers["accept"] = "application/json"
    elif api_key and api_key != "your_tmdb_api_key_here":
        params["api_key"] = api_key
    else:
        print("[WARNING] Aucune clé API TMDB ou jeton d'accès n'a été configuré dans le fichier .env.")
        print("Veuillez configurer TMDB_READ_TOKEN ou TMDB_API_KEY dans votre fichier .env.")
    
    return headers, params

def load_unique_titles(data_dir):
    """
    Charge les fichiers CSV bruts de Netflix, Disney+ et Amazon Prime.
    Filtre et renvoie une liste de dictionnaires contenant les titres uniques avec leur type et année.
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
                # Lire uniquement les colonnes nécessaires pour économiser de la mémoire
                df = pd.read_csv(path, usecols=["type", "title", "release_year"])
                df["platform"] = platform
                dfs.append(df)
                print(f"[INFO] Chargé {len(df)} lignes depuis {os.path.basename(path)}")
            except Exception as e:
                print(f"[ERROR] Impossible de charger {path} : {e}")
        else:
            print(f"[WARNING] Le fichier {path} n'existe pas.")

    if not dfs:
        print("[ERROR] Aucun fichier CSV trouvé dans data/raw/. Arrêt du script.")
        return []

    # Fusionner et dédupliquer
    combined_df = pd.concat(dfs, ignore_index=True)
    
    # Nettoyage de base : supprimer les lignes sans titre ou type
    combined_df = combined_df.dropna(subset=["title", "type"])
    
    # Déduplication sur le titre, le type et l'année de sortie
    unique_df = combined_df.drop_duplicates(subset=["title", "type", "release_year"]).copy()
    
    print(f"[INFO] Total titres chargés : {len(combined_df)}")
    print(f"[INFO] Titres uniques à traiter : {len(unique_df)}")
    
    return unique_df.to_dict(orient="records")

def make_cache_key(title, media_type, release_year):
    """
    Génère une clé de cache normalisée et unique pour chaque titre.
    """
    t_clean = str(title).strip().lower()
    type_clean = "movie" if "movie" in str(media_type).lower() else "tv"
    year_clean = str(int(release_year)) if pd.notna(release_year) else "unknown"
    return f"{type_clean}:{t_clean}:{year_clean}"

def load_cache(cache_path):
    """
    Charge le cache des réponses brutes TMDB s'il existe.
    """
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            print(f"[INFO] Cache chargé avec {len(cache)} entrées depuis {cache_path}")
            return cache
        except Exception as e:
            print(f"[ERROR] Erreur lors du chargement du cache {cache_path} : {e}. Création d'un nouveau cache.")
    return {}

def save_cache(cache, cache_path):
    """
    Sauvegarde le cache de données brutes dans le fichier JSON.
    """
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Cache sauvegardé avec succès ({len(cache)} entrées) dans {cache_path}")
    except Exception as e:
        print(f"[ERROR] Échec de la sauvegarde du cache : {e}")

def query_tmdb_api(title, media_type, release_year, headers, base_params, delay=0.1):
    """
    Interroge l'API TMDB pour un titre donné.
    Effectue une recherche puis récupère les détails complets du premier résultat.
    Retourne un dictionnaire {'search_result': ..., 'details': ...} ou None si non trouvé.
    """
    # 1. Normalisation du type de média
    tmdb_type = "movie" if "movie" in str(media_type).lower() else "tv"
    
    # 2. Construction de la requête de recherche
    search_url = f"{TMDB_BASE_URL}/search/{tmdb_type}"
    
    # Paramètres de recherche
    params = base_params.copy()
    params["query"] = title
    
    if pd.notna(release_year):
        year_val = int(release_year)
        if tmdb_type == "movie":
            params["year"] = year_val
        else:
            params["first_air_date_year"] = year_val

    # Gestion du rate limit et des erreurs
    max_retries = 3
    for attempt in range(max_retries):
        try:
            time.sleep(delay)
            response = requests.get(search_url, headers=headers, params=params, timeout=10)
            
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 5))
                print(f"[WARNING] Rate limit atteint (HTTP 429). Pause de {retry_after}s (tentative {attempt+1}/{max_retries})...")
                time.sleep(retry_after)
                continue
                
            if response.status_code != 200:
                print(f"[ERROR] API a renvoyé le statut {response.status_code} pour '{title}'")
                return None
                
            search_data = response.json()
            results = search_data.get("results", [])
            
            if not results:
                # Si aucun résultat avec l'année, essayer sans l'année
                if "year" in params or "first_air_date_year" in params:
                    params_no_year = base_params.copy()
                    params_no_year["query"] = title
                    response_no_year = requests.get(search_url, headers=headers, params=params_no_year, timeout=10)
                    if response_no_year.status_code == 200:
                        results = response_no_year.json().get("results", [])
                
                if not results:
                    return {"search_result": None, "details": None}
            
            # Prendre le premier résultat de recherche
            best_match = results[0]
            tmdb_id = best_match.get("id")
            
            # 3. Récupérer les détails détaillés (ex: budget, revenue, runtime, etc.)
            details_url = f"{TMDB_BASE_URL}/{tmdb_type}/{tmdb_id}"
            time.sleep(delay)
            details_response = requests.get(details_url, headers=headers, params=base_params, timeout=10)
            
            details_data = None
            if details_response.status_code == 200:
                details_data = details_response.json()
            else:
                print(f"[WARNING] Impossible de récupérer les détails pour ID {tmdb_id} (Code {details_response.status_code})")
                
            return {
                "search_result": best_match,
                "details": details_data
            }
            
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Erreur réseau lors de la recherche de '{title}' (tentative {attempt+1}/{max_retries}) : {e}")
            time.sleep(2)
            
    return None

def main():
    parser = argparse.ArgumentParser(description="Extraction de données complémentaires depuis l'API TMDB.")
    parser.add_argument("--limit", type=int, default=100, help="Nombre maximum de nouveaux titres à interroger (par défaut: 100).")
    parser.add_argument("--all", action="store_true", help="Traiter tous les titres restants sans limite.")
    parser.add_argument("--save-every", type=int, default=50, help="Fréquence de sauvegarde du cache (par défaut : toutes les 50 requêtes).")
    parser.add_argument("--delay", type=float, default=0.1, help="Délai en secondes entre les requêtes API (par défaut : 0.1).")
    
    args = parser.parse_args()

    # Déterminer les chemins de fichiers
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raw_data_dir = os.path.join(base_dir, "data", "raw")
    cache_file_path = os.path.join(raw_data_dir, "tmdb_raw_responses.json")

    print("[INFO] Démarrage de l'extraction TMDB...")
    
    # 1. Charger les titres uniques
    titles_to_process = load_unique_titles(raw_data_dir)
    if not titles_to_process:
        return

    # 2. Configurer l'authentification
    headers, base_params = get_auth_config()
    if not headers and not base_params:
        print("[ERROR] Authentification non configurée. Veuillez renseigner le fichier .env.")
        return

    # 3. Charger le cache existant
    cache = load_cache(cache_file_path)

    # 4. Traitement des titres
    api_calls_count = 0
    newly_saved_count = 0
    skipped_count = 0
    not_found_count = 0

    max_calls = float('inf') if args.all else args.limit
    
    print(f"[INFO] Traitement en cours (Limite max de requêtes : {'Illimitée' if args.all else args.limit})...")
    
    try:
        for idx, item in enumerate(titles_to_process):
            title = item["title"]
            media_type = item["type"]
            release_year = item["release_year"]
            
            cache_key = make_cache_key(title, media_type, release_year)
            
            # Si le titre est déjà dans le cache (qu'il ait été trouvé ou non)
            if cache_key in cache:
                skipped_count += 1
                if cache[cache_key] and cache[cache_key].get("search_result") is None:
                    not_found_count += 1
                continue
            
            # Vérifier si on a atteint la limite de requêtes pour cette exécution
            if api_calls_count >= max_calls:
                print(f"[INFO] Limite de requêtes de {args.limit} atteinte pour cette session.")
                break

            print(f"[{api_calls_count+1}/{max_calls}] Interrogation TMDB pour : {title} ({release_year}) [{media_type}]")
            
            # Interroger l'API
            tmdb_response = query_tmdb_api(
                title=title,
                media_type=media_type,
                release_year=release_year,
                headers=headers,
                base_params=base_params,
                delay=args.delay
            )
            
            if tmdb_response is not None:
                # Sauvegarder dans le cache local
                cache[cache_key] = tmdb_response
                api_calls_count += 1
                newly_saved_count += 1
                
                if tmdb_response.get("search_result") is None:
                    print(f"  -> Non trouvé sur TMDB.")
                    not_found_count += 1
                else:
                    print(f"  -> Trouvé ! TMDB ID: {tmdb_response['search_result'].get('id')}")
            else:
                print(f"  -> Échec de la requête pour : {title} (sera retenté au prochain lancement).")

            # Sauvegarde incrémentale toutes les N requêtes
            if api_calls_count > 0 and api_calls_count % args.save_every == 0:
                save_cache(cache, cache_file_path)

    except KeyboardInterrupt:
        print("\n[WARNING] Interruption par l'utilisateur. Sauvegarde du cache en cours...")
    finally:
        # Toujours sauvegarder le cache à la fin ou en cas d'interruption
        if newly_saved_count > 0:
            save_cache(cache, cache_file_path)
            
        print("\n=== Bilan de l'extraction ===")
        print(f"Titres déjà en cache : {skipped_count}")
        print(f"Nouveaux appels API passés : {api_calls_count}")
        print(f"Nouveaux titres enregistrés : {newly_saved_count}")
        print(f"Titres non trouvés sur TMDB (total historique) : {not_found_count}")
        print(f"Total des titres dans le cache final : {len(cache)}")
        print("=============================")

if __name__ == "__main__":
    main()
