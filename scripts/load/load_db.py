#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de Chargement (Load) dans la base de données.
Ce script lit les fichiers du dossier data/processed/ et les injecte
de manière relationnelle et sécurisée dans la base SQLite pipeline_etl.db
en respectant le schéma en étoile.
"""

import os
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

def run_sql_script(conn, sql_path):
    """
    Exécute le script SQL de création de tables.
    """
    print(f"[INFO] Initialisation des tables depuis {os.path.basename(sql_path)}...")
    if not os.path.exists(sql_path):
        print(f"[ERROR] Script SQL introuvable : {sql_path}")
        return False
        
    with open(sql_path, "r", encoding="utf-8") as f:
        sql_commands = f.read()
        
    # Séparer les requêtes par point-virgule et les exécuter
    # Utiliser le DBAPI sous-jacent pour exécuter les scripts multiples proprement sous SQLite
    try:
        raw_conn = conn.connection
        raw_conn.executescript(sql_commands)
        print("[INFO] Tables initialisées avec succès.")
        return True
    except Exception as e:
        # En cas d'erreur de compatibilité DBAPI, fallback sur la découpe manuelle
        print(f"[WARNING] Erreur DBAPI ({e}). Fallback sur l'exécution séquentielle...")
        try:
            for command in sql_commands.split(";"):
                clean_command = command.strip()
                if clean_command:
                    conn.execute(text(clean_command))
            print("[INFO] Tables initialisées avec succès (séquentiel).")
            return True
        except Exception as err:
            print(f"[ERROR] Impossible de créer les tables : {err}")
            return False

def load_data_to_db(processed_dir, sql_path):
    """
    Lit les CSV dans processed/ et les injecte dans la base de données.
    """
    db_path = os.path.join(processed_dir, "pipeline_etl.db")
    engine = create_engine(f"sqlite:///{db_path}")
    
    catalog_path = os.path.join(processed_dir, "unified_catalog.csv")
    mapping_path = os.path.join(processed_dir, "platform_mapping.csv")
    
    if not os.path.exists(catalog_path) or not os.path.exists(mapping_path):
        print("[ERROR] Fichiers processed manquants. Veuillez lancer la transformation d'abord.")
        return
        
    # Charger les DataFrames
    print("[INFO] Lecture des données transformées...")
    catalog_df = pd.read_csv(catalog_path)
    mapping_df = pd.read_csv(mapping_path)
    
    # Remplacer les valeurs NaN par None pour une insertion SQL propre (NULL)
    catalog_df = catalog_df.replace({np.nan: None})
    mapping_df = mapping_df.replace({np.nan: None})
    
    with engine.begin() as conn:
        # 1. Initialiser le schéma SQL
        if not run_sql_script(conn, sql_path):
            print("[ERROR] Échec de l'initialisation du schéma. Arrêt.")
            return

        # Vider les tables existantes pour repartir à propre (Load complet)
        print("[INFO] Nettoyage des tables existantes...")
        conn.execute(text("DELETE FROM Lien_Film_Genre;"))
        conn.execute(text("DELETE FROM Lien_Film_Plateforme;"))
        conn.execute(text("DELETE FROM Dim_Genres;"))
        conn.execute(text("DELETE FROM Dim_Plateformes;"))
        conn.execute(text("DELETE FROM Fact_Films;"))
        
        # 2. Insérer Dim_Plateformes
        print("[INFO] Insertion de Dim_Plateformes...")
        platform_names = ["Netflix", "Prime Video", "Disney+"]
        for p_name in platform_names:
            conn.execute(
                text("INSERT INTO Dim_Plateformes (nom) VALUES (:nom);"),
                {"nom": p_name}
            )
            
        # Récupérer les correspondances ID-Nom de Plateforme
        res_plat = conn.execute(text("SELECT plateforme_id, nom FROM Dim_Plateformes;")).fetchall()
        platform_id_map = {row[1]: row[0] for row in res_plat}
        # Dictionnaire interne de mapping
        platform_internal_map = {
            "netflix": platform_id_map["Netflix"],
            "amazon": platform_id_map["Prime Video"],
            "disney": platform_id_map["Disney+"]
        }

        # 3. Extraire et insérer Dim_Genres
        print("[INFO] Extraction et insertion de Dim_Genres...")
        all_genres = set()
        
        # Parcourir 'listed_in' (plateformes) et 'tmdb_genres' (TMDB)
        for _, row in catalog_df.iterrows():
            # Genres plateforme
            if row["listed_in"]:
                for g in str(row["listed_in"]).split(","):
                    all_genres.add(g.strip())
            # Genres TMDB
            if row["tmdb_genres"]:
                for g in str(row["tmdb_genres"]).split(","):
                    all_genres.add(g.strip())
                    
        # Retirer les valeurs vides
        all_genres = {g for g in all_genres if g}
        
        for g_name in sorted(all_genres):
            conn.execute(
                text("INSERT INTO Dim_Genres (nom) VALUES (:nom);"),
                {"nom": g_name}
            )
            
        # Récupérer les correspondances ID-Nom de Genre
        res_gen = conn.execute(text("SELECT genre_id, nom FROM Dim_Genres;")).fetchall()
        genre_id_map = {row[1]: row[0] for row in res_gen}

        # 4. Insérer Fact_Films
        print(f"[INFO] Insertion de {len(catalog_df)} films dans Fact_Films...")
        
        # Requête paramétrée sécurisée
        insert_film_sql = text("""
            INSERT INTO Fact_Films (
                film_id, titre, type, budget, revenu_box_office, note_moyenne, date_sortie,
                imdb_id, note_letterboxd, votes_letterboxd, fans_letterboxd, rentabilite, benefice
            ) VALUES (
                :film_id, :titre, :type, :budget, :revenu_box_office, :note_moyenne, :date_sortie,
                :imdb_id, :note_letterboxd, :votes_letterboxd, :fans_letterboxd, :rentabilite, :benefice
            );
        """)
        
        # Batch insert pour de meilleures performances
        films_batch = []
        for _, row in catalog_df.iterrows():
            films_batch.append({
                "film_id": row["unified_id"],
                "titre": row["title"],
                "type": row["type"],
                "budget": row["tmdb_budget"],
                "revenu_box_office": row["tmdb_revenue"],
                "note_moyenne": row["tmdb_rating"],
                "date_sortie": str(row["release_year"]),
                "imdb_id": row["imdb_id"],
                "note_letterboxd": row["letterboxd_rating"],
                "votes_letterboxd": row["letterboxd_vote_count"],
                "fans_letterboxd": row["letterboxd_fans_count"],
                "rentabilite": row["rentability"],
                "benefice": row["profit"]
            })
            
        # Exécuter l'insertion de masse
        conn.execute(insert_film_sql, films_batch)

        # 5. Insérer les liaisons Lien_Film_Plateforme
        print("[INFO] Insertion des liaisons Lien_Film_Plateforme...")
        
        insert_link_plat_sql = text("""
            INSERT INTO Lien_Film_Plateforme (
                film_id, plateforme_id, date_ajout_catalogue
            ) VALUES (:film_id, :plateforme_id, :date_ajout_catalogue);
        """)
        
        # Créer un dictionnaire pour récupérer la date d'ajout
        # Clé: unified_id, Valeur: date_added
        date_added_map = {row["unified_id"]: row["date_added"] for _, row in catalog_df.iterrows()}
        
        seen_links = set()
        links_plat_batch = []
        for _, row in mapping_df.iterrows():
            u_id = row["unified_id"]
            raw_plat = row["platform"]
            
            p_id = platform_internal_map.get(raw_plat)
            if p_id:
                link_key = (u_id, p_id)
                if link_key not in seen_links:
                    seen_links.add(link_key)
                    links_plat_batch.append({
                        "film_id": u_id,
                        "plateforme_id": p_id,
                        "date_ajout_catalogue": date_added_map.get(u_id)
                    })
                
        # Insérer les liaisons de masse
        conn.execute(insert_link_plat_sql, links_plat_batch)

        # 6. Insérer les liaisons Lien_Film_Genre
        print("[INFO] Insertion des liaisons Lien_Film_Genre...")
        
        insert_link_gen_sql = text("""
            INSERT INTO Lien_Film_Genre (film_id, genre_id)
            VALUES (:film_id, :genre_id);
        """)
        
        links_gen_batch = []
        for _, row in catalog_df.iterrows():
            u_id = row["unified_id"]
            genres_found = set()
            
            # Récupérer tous les genres de cette ligne
            if row["listed_in"]:
                for g in str(row["listed_in"]).split(","):
                    genres_found.add(g.strip())
            if row["tmdb_genres"]:
                for g in str(row["tmdb_genres"]).split(","):
                    genres_found.add(g.strip())
                    
            for g_name in genres_found:
                if g_name in genre_id_map:
                    links_gen_batch.append({
                        "film_id": u_id,
                        "genre_id": genre_id_map[g_name]
                    })
                    
        # Insérer les liaisons de masse (dédupliquées par couple)
        unique_links_gen = [dict(t) for t in {tuple(d.items()) for d in links_gen_batch}]
        conn.execute(insert_link_gen_sql, unique_links_gen)

    print(f"[SUCCESS] Données injectées avec succès dans la base de données relationnelle sous : {db_path}")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    processed_dir = os.path.join(base_dir, "data", "processed")
    sql_path = os.path.join(base_dir, "sql", "create_tables.sql")
    
    load_data_to_db(processed_dir, sql_path)

if __name__ == "__main__":
    main()
