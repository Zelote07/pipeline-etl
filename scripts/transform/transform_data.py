#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de Transformation, Fuzzy Matching et Enrichissement.
Ce script :
1. Charge les 3 catalogues bruts (Netflix, Disney+, Amazon Prime).
2. Nettoie les données (types, formats de date YYYY-MM-DD, valeurs aberrantes).
3. Fusionne les doublons entre plateformes à l'aide de thefuzz (Fuzzy Matching)
   sur les titres de la même année et du même type.
4. Croise les données unifiées avec les caches TMDB et Letterboxd pour les enrichir.
5. Calcule de nouvelles variables (rentabilité, bénéfice, indicateurs de plateforme).
6. Exporte le catalogue unifié et la table de correspondance dans data/processed/.
"""

import os
import json
import re
import pandas as pd
import numpy as np
from thefuzz import fuzz
from thefuzz import process

def clean_date(date_str):
    """
    Convertit proprement les dates textuelles au format standard YYYY-MM-DD.
    """
    if pd.isna(date_str) or not str(date_str).strip():
        return np.nan
    try:
        # pd.to_datetime gère les formats comme "September 25, 2021" ou "2021-11-26"
        dt = pd.to_datetime(str(date_str).strip(), errors='coerce')
        if pd.isna(dt):
            return np.nan
        return dt.strftime('%Y-%m-%d')
    except Exception:
        return np.nan

def clean_text_list(text_str):
    """
    Nettoie et normalise les listes de chaînes séparées par des virgules (directors, cast, countries).
    """
    if pd.isna(text_str) or not str(text_str).strip():
        return ""
    # Séparer, nettoyer les espaces et enlever les doublons tout en gardant l'ordre
    items = [i.strip() for i in str(text_str).split(',') if i.strip()]
    seen = set()
    unique_items = []
    for item in items:
        if item.lower() not in seen:
            seen.add(item.lower())
            unique_items.append(item)
    return ", ".join(unique_items)

def merge_text_fields(field1, field2):
    """
    Fusionne deux chaînes de texte séparées par des virgules en évitant les doublons.
    """
    if not field1 or pd.isna(field1):
        return field2 if pd.notna(field2) else ""
    if not field2 or pd.isna(field2):
        return field1 if pd.notna(field1) else ""
    
    combined = str(field1) + ", " + str(field2)
    return clean_text_list(combined)

def normalize_title(title):
    """
    Normalisation de base pour faciliter les premières comparaisons exactes.
    """
    t = str(title).lower().strip()
    t = re.sub(r'[^a-z0-9\s]', '', t) # Enlever ponctuation
    t = re.sub(r'\s+', ' ', t) # Homogénéiser les espaces
    return t.strip()

def load_and_clean_platforms(data_dir):
    """
    Charge et applique le nettoyage de base sur les 3 catalogues de plateformes.
    """
    platforms = {
        "netflix": "netflix_titles.csv",
        "disney": "disney_plus_titles.csv",
        "amazon": "amazon_prime_titles.csv"
    }
    
    cleaned_dfs = []
    
    for platform_name, file_name in platforms.items():
        path = os.path.join(data_dir, file_name)
        if not os.path.exists(path):
            print(f"[WARNING] Catalogue {file_name} manquant. Passage au suivant.")
            continue
            
        print(f"[INFO] Chargement et nettoyage de {file_name}...")
        df = pd.read_csv(path)
        
        # Supprimer le synopsis original (comme demandé par l'utilisateur, sauf si NLP désiré)
        if "description" in df.columns:
            df = df.drop(columns=["description"])
            
        # Nettoyer les colonnes textuelles de base
        df["title_clean"] = df["title"].apply(normalize_title)
        df["type"] = df["type"].fillna("Movie")
        # Standardiser la date
        df["date_added_clean"] = df["date_added"].apply(clean_date)
        df["release_year"] = pd.to_numeric(df["release_year"], errors='coerce').fillna(0).astype(int)
        
        # Normaliser les listes textuelles
        for col in ["director", "cast", "country", "listed_in"]:
            if col in df.columns:
                df[col] = df[col].apply(clean_text_list)
                
        df["platform"] = platform_name
        cleaned_dfs.append(df)
        
    return cleaned_dfs

def perform_fuzzy_matching(dfs, similarity_threshold=90):
    """
    Regroupe et fusionne les titres des 3 plateformes par Fuzzy Matching.
    Retourne le catalogue unifié et la table de correspondance plateforme-catalogue.
    """
    print("[INFO] Démarrage du processus de fusion par Fuzzy Matching...")
    
    # Concaténer tous les catalogues nettoyés
    all_titles = pd.concat(dfs, ignore_index=True)
    
    # Créer un dictionnaire pour stocker les groupes unifiés
    # Clé: unique_id, Valeur: dictionnaire des métadonnées fusionnées
    unified_catalog = {}
    
    # Table de correspondance plateforme -> catalogue unifié
    # Liste de dicts: [{'unified_id': ..., 'platform': ..., 'show_id': ..., 'original_title': ...}]
    platform_mappings = []
    
    unified_counter = 1
    
    # Groupement par Type et Année de sortie pour limiter le volume de calcul N^2
    grouped = all_titles.groupby(["type", "release_year"])
    
    total_groups = len(grouped)
    current_group_idx = 0
    
    for (media_type, release_year), group in grouped:
        current_group_idx += 1
        if current_group_idx % 50 == 0 or current_group_idx == total_groups:
            print(f"  -> Traitement des groupes de type/année : {current_group_idx}/{total_groups}...")
            
        # Liste locale des titres déjà unifiés dans ce groupe spécifique
        # Chaque élément est un dict représentant un film unifié dans cette année/type
        local_unified = []
        
        for _, row in group.iterrows():
            title = row["title"]
            title_clean = row["title_clean"]
            
            # 1. Tenter d'abord une correspondance exacte sur le titre normalisé
            matched_idx = None
            for idx, u_movie in enumerate(local_unified):
                if u_movie["title_clean"] == title_clean:
                    matched_idx = idx
                    break
            
            # 2. Si pas de correspondance exacte, tenter le Fuzzy Matching
            if matched_idx is None and len(local_unified) > 0:
                # Extraire uniquement les titres textuels pour thefuzz
                existing_titles = [u["title"] for u in local_unified]
                best_match, score = process.extractOne(title, existing_titles, scorer=fuzz.token_sort_ratio)
                
                if score >= similarity_threshold:
                    # Trouver l'index correspondant dans local_unified
                    for idx, u_movie in enumerate(local_unified):
                        if u_movie["title"] == best_match:
                            matched_idx = idx
                            break
            
            # 3. Si trouvé (exact ou fuzzy), fusionner les métadonnées
            if matched_idx is not None:
                u_id = local_unified[matched_idx]["unified_id"]
                # Fusionner les champs textuels
                local_unified[matched_idx]["director"] = merge_text_fields(local_unified[matched_idx]["director"], row.get("director", ""))
                local_unified[matched_idx]["cast"] = merge_text_fields(local_unified[matched_idx]["cast"], row.get("cast", ""))
                local_unified[matched_idx]["country"] = merge_text_fields(local_unified[matched_idx]["country"], row.get("country", ""))
                local_unified[matched_idx]["listed_in"] = merge_text_fields(local_unified[matched_idx]["listed_in"], row.get("listed_in", ""))
                
                # Conserver la date d'ajout la plus ancienne ou non nulle
                if pd.notna(row["date_added_clean"]):
                    current_date = local_unified[matched_idx]["date_added"]
                    if pd.isna(current_date) or row["date_added_clean"] < current_date:
                        local_unified[matched_idx]["date_added"] = row["date_added_clean"]
                
                # Enregistrer le titre d'origine dans les variantes
                local_unified[matched_idx]["title_variants"].add(title)
                
            # 4. Si non trouvé, créer une nouvelle entrée unifiée
            else:
                u_id = f"UT{unified_counter:05d}"
                new_entry = {
                    "unified_id": u_id,
                    "title": title,
                    "title_clean": title_clean,
                    "type": media_type,
                    "release_year": release_year,
                    "director": row.get("director", ""),
                    "cast": row.get("cast", ""),
                    "country": row.get("country", ""),
                    "listed_in": row.get("listed_in", ""),
                    "date_added": row["date_added_clean"],
                    "title_variants": {title}
                }
                local_unified.append(new_entry)
                unified_counter += 1
                
            # Ajouter la correspondance de plateforme
            platform_mappings.append({
                "unified_id": u_id,
                "platform": row["platform"],
                "show_id": row["show_id"],
                "original_title": title,
                "original_year": release_year
            })
            
        # Réinjecter le groupe local dans le catalogue global
        for u_movie in local_unified:
            # Convertir le set de variantes de titres en liste pour la sérialisation
            u_movie["title_variants"] = list(u_movie["title_variants"])
            unified_catalog[u_movie["unified_id"]] = u_movie
            
    print(f"[INFO] Fusion terminée. {len(all_titles)} lignes d'origine réduites à {len(unified_catalog)} titres uniques.")
    
    return pd.DataFrame(unified_catalog.values()), pd.DataFrame(platform_mappings)

def load_raw_caches(raw_dir):
    """
    Charge les fichiers JSON contenant les réponses brutes TMDB et Letterboxd.
    """
    tmdb_cache = {}
    letterboxd_cache = {}
    
    tmdb_path = os.path.join(raw_dir, "tmdb_raw_responses.json")
    if os.path.exists(tmdb_path):
        try:
            with open(tmdb_path, "r", encoding="utf-8") as f:
                tmdb_cache = json.load(f)
            print(f"[INFO] Cache TMDB chargé ({len(tmdb_cache)} entrées).")
        except Exception as e:
            print(f"[ERROR] Impossible de charger le cache TMDB : {e}")
            
    lb_path = os.path.join(raw_dir, "letterboxd_raw_responses.json")
    if os.path.exists(lb_path):
        try:
            with open(lb_path, "r", encoding="utf-8") as f:
                letterboxd_cache = json.load(f)
            print(f"[INFO] Cache Letterboxd chargé ({len(letterboxd_cache)} entrées).")
        except Exception as e:
            print(f"[ERROR] Impossible de charger le cache Letterboxd : {e}")
            
    return tmdb_cache, letterboxd_cache

def enrich_unified_catalog(catalog_df, mapping_df, tmdb_cache, letterboxd_cache):
    """
    Enrichit chaque titre du catalogue avec les données de TMDB et Letterboxd.
    """
    print("[INFO] Démarrage de l'enrichissement des données...")
    
    # 1. Déterminer la présence sur les plateformes
    # Créer des variables booléennes de disponibilité
    catalog_df["on_netflix"] = False
    catalog_df["on_disney_plus"] = False
    catalog_df["on_amazon_prime"] = False
    
    for _, mapping in mapping_df.iterrows():
        u_id = mapping["unified_id"]
        platform = mapping["platform"]
        
        idx = catalog_df[catalog_df["unified_id"] == u_id].index
        if len(idx) > 0:
            if platform == "netflix":
                catalog_df.at[idx[0], "on_netflix"] = True
            elif platform == "disney":
                catalog_df.at[idx[0], "on_disney_plus"] = True
            elif platform == "amazon":
                catalog_df.at[idx[0], "on_amazon_prime"] = True

    # 2. Définir les nouvelles colonnes d'enrichissement
    enrich_cols = {
        "tmdb_id": np.nan, "imdb_id": None, "tmdb_genres": None,
        "tmdb_rating": np.nan, "tmdb_vote_count": np.nan, "tmdb_popularity": np.nan,
        "tmdb_budget": np.nan, "tmdb_revenue": np.nan, "tmdb_runtime": np.nan,
        "letterboxd_rating": np.nan, "letterboxd_vote_count": np.nan, "letterboxd_fans_count": np.nan,
        "letterboxd_url": None
    }
    
    for col, default in enrich_cols.items():
        catalog_df[col] = default
        
    # Compteurs d'enrichissement pour le bilan final
    enriched_tmdb_count = 0
    enriched_lb_count = 0

    # 3. Parcourir et croiser avec les caches
    for idx, row in catalog_df.iterrows():
        u_id = row["unified_id"]
        media_type = row["type"]
        release_year = row["release_year"]
        
        # Le type dans la clé de cache est 'movie' ou 'tv'
        cache_type = "movie" if "movie" in str(media_type).lower() else "tv"
        
        # Un titre unifié peut avoir plusieurs variantes (ex: "Spider-Man", "Spiderman")
        # On va chercher dans le cache pour toutes les variantes possibles
        variants = row["title_variants"]
        
        # A. CROISEMENT TMDB
        tmdb_data = None
        for title_var in variants:
            t_clean = str(title_var).strip().lower()
            cache_key = f"{cache_type}:{t_clean}:{release_year}"
            
            if cache_key in tmdb_cache:
                entry = tmdb_cache[cache_key]
                # S'assurer que le titre a bien été trouvé sur TMDB
                if entry and entry.get("search_result") is not None:
                    tmdb_data = entry
                    break
        
        if tmdb_data:
            enriched_tmdb_count += 1
            search_res = tmdb_data.get("search_result", {})
            details = tmdb_data.get("details", {}) or {}
            
            catalog_df.at[idx, "tmdb_id"] = search_res.get("id")
            catalog_df.at[idx, "tmdb_rating"] = search_res.get("vote_average")
            catalog_df.at[idx, "tmdb_vote_count"] = search_res.get("vote_count")
            catalog_df.at[idx, "tmdb_popularity"] = search_res.get("popularity")
            
            # Métadonnées détaillées
            catalog_df.at[idx, "tmdb_runtime"] = details.get("runtime")
            
            # Genres TMDB propres
            genres_list = [g.get("name") for g in details.get("genres", []) if g.get("name")]
            if genres_list:
                catalog_df.at[idx, "tmdb_genres"] = ", ".join(genres_list)
            
            # Nettoyage Budget/Revenu : remplacer les 0 par NaN
            budget = details.get("budget", 0)
            revenue = details.get("revenue", 0)
            
            catalog_df.at[idx, "tmdb_budget"] = budget if budget > 0 else np.nan
            catalog_df.at[idx, "tmdb_revenue"] = revenue if revenue > 0 else np.nan

        # B. CROISEMENT LETTERBOXD (Uniquement si de type Movie)
        lb_data = None
        if cache_type == "movie":
            for title_var in variants:
                t_clean = str(title_var).strip().lower()
                cache_key = f"movie:{t_clean}:{release_year}"
                
                if cache_key in letterboxd_cache:
                    entry = letterboxd_cache[cache_key]
                    if entry and entry.get("found", False):
                        lb_data = entry
                        break
                        
        if lb_data:
            enriched_lb_count += 1
            catalog_df.at[idx, "letterboxd_url"] = lb_data.get("movie_url")
            catalog_df.at[idx, "letterboxd_rating"] = lb_data.get("rating_value")
            catalog_df.at[idx, "letterboxd_vote_count"] = lb_data.get("rating_count")
            catalog_df.at[idx, "letterboxd_fans_count"] = lb_data.get("likes_count")
            
            # Récupérer l'ID IMDb si présent sur Letterboxd
            if pd.isna(catalog_df.at[idx, "imdb_id"]) and lb_data.get("imdb_id"):
                catalog_df.at[idx, "imdb_id"] = lb_data.get("imdb_id")
                
            # Si l'ID TMDB n'était pas trouvé, utiliser celui résolu par Letterboxd
            if pd.isna(catalog_df.at[idx, "tmdb_id"]) and lb_data.get("tmdb_id"):
                catalog_df.at[idx, "tmdb_id"] = lb_data.get("tmdb_id")

    # 4. Calcul de nouvelles variables (Enrichissement)
    # Rentabilité = Revenu / Budget
    catalog_df["rentability"] = catalog_df["tmdb_revenue"] / catalog_df["tmdb_budget"]
    
    # Bénéfice = Revenu - Budget
    catalog_df["profit"] = catalog_df["tmdb_revenue"] - catalog_df["tmdb_budget"]
    
    # Supprimer les colonnes temporaires
    catalog_df = catalog_df.drop(columns=["title_clean", "title_variants"])
    
    print("=== Bilan de l'enrichissement ===")
    print(f"Titres enrichis avec TMDB : {enriched_tmdb_count} / {len(catalog_df)}")
    print(f"Titres enrichis avec Letterboxd : {enriched_lb_count} / {len(catalog_df)}")
    print("=================================")
    
    return catalog_df

def main():
    # Déterminer les dossiers
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raw_data_dir = os.path.join(base_dir, "data", "raw")
    processed_data_dir = os.path.join(base_dir, "data", "processed")
    
    os.makedirs(processed_data_dir, exist_ok=True)
    
    # 1. Charger et nettoyer les données des plateformes
    cleaned_dfs = load_and_clean_platforms(raw_data_dir)
    if not cleaned_dfs:
        print("[ERROR] Aucun catalogue disponible pour la transformation. Arrêt.")
        return
        
    # 2. Effectuer la fusion par Fuzzy Matching (seuil par défaut : 90%)
    unified_df, mapping_df = perform_fuzzy_matching(cleaned_dfs, similarity_threshold=90)
    
    # 3. Charger les caches bruts d'extraction
    tmdb_cache, letterboxd_cache = load_raw_caches(raw_data_dir)
    
    # 4. Enrichir les données
    final_catalog_df = enrich_unified_catalog(unified_df, mapping_df, tmdb_cache, letterboxd_cache)
    
    # 5. Exporter les résultats finals dans data/processed/
    catalog_out_path = os.path.join(processed_data_dir, "unified_catalog.csv")
    mapping_out_path = os.path.join(processed_data_dir, "platform_mapping.csv")
    
    try:
        final_catalog_df.to_csv(catalog_out_path, index=False, encoding="utf-8")
        mapping_df.to_csv(mapping_out_path, index=False, encoding="utf-8")
        print(f"[SUCCESS] Catalogue unifié enregistré dans : {catalog_out_path}")
        print(f"[SUCCESS] Table de correspondance enregistrée dans : {mapping_out_path}")
    except Exception as e:
        print(f"[ERROR] Échec de l'exportation des fichiers processed : {e}")

if __name__ == "__main__":
    main()
