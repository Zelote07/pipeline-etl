#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Serveur Backend Flask pour le Dashboard de Supervision ETL.
Expose des API REST JSON pour lire les données de la base relationnelle SQLite pipeline_etl.db
et fournit l'interface utilisateur web.
"""

import os
import json
import sqlite3
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder='templates')

# Chemins des fichiers du projet
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "processed", "pipeline_etl.db")
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")

def get_db_connection():
    """
    Crée une connexion SQLite standard.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_file_size_str(filepath):
    """
    Retourne la taille d'un fichier dans une chaîne lisible (KB, MB).
    """
    if not os.path.exists(filepath):
        return "0 KB"
    size_bytes = os.path.getsize(filepath)
    if size_bytes == 0:
        return "0 KB"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

@app.route('/')
def index():
    """
    Page d'accueil du Dashboard.
    """
    return render_template('index.html')

@app.route('/api/stats')
def get_stats():
    """
    Fournit les statistiques globales pour les indicateurs (KPIs) et les graphiques.
    """
    if not os.path.exists(DB_PATH):
        return jsonify({
            "total_movies": 0, "total_genres": 0, "total_budget": 0, "total_revenue": 0,
            "platform_distribution": {}, "genre_distribution": [], "genre_list": []
        })

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. KPIs
    total_movies = cursor.execute("SELECT COUNT(*) FROM Fact_Films").fetchone()[0]
    total_genres = cursor.execute("SELECT COUNT(*) FROM Dim_Genres").fetchone()[0]
    total_budget = cursor.execute("SELECT SUM(budget) FROM Fact_Films").fetchone()[0] or 0
    total_revenue = cursor.execute("SELECT SUM(revenu_box_office) FROM Fact_Films").fetchone()[0] or 0
    
    # 2. Répartition Plateformes
    netflix_count = cursor.execute("""
        SELECT COUNT(*) FROM Lien_Film_Plateforme l 
        JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
        WHERE p.nom = 'Netflix'
    """).fetchone()[0]
    prime_count = cursor.execute("""
        SELECT COUNT(*) FROM Lien_Film_Plateforme l 
        JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
        WHERE p.nom = 'Prime Video'
    """).fetchone()[0]
    disney_count = cursor.execute("""
        SELECT COUNT(*) FROM Lien_Film_Plateforme l 
        JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
        WHERE p.nom = 'Disney+'
    """).fetchone()[0]
    
    platform_dist = {
        "Netflix": netflix_count,
        "Prime Video": prime_count,
        "Disney+": disney_count
    }
    
    # 3. Répartition Genres (Top 10)
    genre_rows = cursor.execute("""
        SELECT g.nom, COUNT(lg.film_id) as count
        FROM Lien_Film_Genre lg
        JOIN Dim_Genres g ON lg.genre_id = g.genre_id
        GROUP BY g.nom
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()
    
    genre_dist = [{"name": row["nom"], "count": row["count"]} for row in genre_rows]
    
    # 4. Liste complète de tous les genres pour les filtres
    genre_list_rows = cursor.execute("SELECT nom FROM Dim_Genres ORDER BY nom ASC").fetchall()
    genre_list = [row["nom"] for row in genre_list_rows]
    
    conn.close()
    
    return jsonify({
        "total_movies": total_movies,
        "total_genres": total_genres,
        "total_budget": total_budget,
        "total_revenue": total_revenue,
        "platform_distribution": platform_dist,
        "genre_distribution": genre_dist,
        "genre_list": genre_list
    })

@app.route('/api/pipeline')
def get_pipeline_info():
    """
    Fournit les statistiques physiques sur la pipeline (tailles des fichiers, entrées cache).
    """
    # Tailles des fichiers CSV bruts d'origine
    csv_files = ["netflix_titles.csv", "disney_plus_titles.csv", "amazon_prime_titles.csv"]
    csv_count = 0
    csv_total_size = 0
    raw_total_rows = 19925  # Total statique de lignes dans nos 3 fichiers bruts d'origine
    
    for filename in csv_files:
        path = os.path.join(RAW_DIR, filename)
        if os.path.exists(path):
            csv_count += 1
            csv_total_size += os.path.getsize(path)
            
    csv_size_str = "0 B"
    if csv_total_size > 0:
        csv_size_bytes = csv_total_size
        for unit in ['B', 'KB', 'MB']:
            if csv_size_bytes < 1024.0:
                csv_size_str = f"{csv_size_bytes:.1f} {unit}"
                break
            csv_size_bytes /= 1024.0
            
    # Cache TMDB
    tmdb_cache_entries = 0
    tmdb_cache_size = "0 B"
    tmdb_path = os.path.join(RAW_DIR, "tmdb_raw_responses.json")
    if os.path.exists(tmdb_path):
        tmdb_cache_size = get_file_size_str(tmdb_path)
        try:
            with open(tmdb_path, "r", encoding="utf-8") as f:
                tmdb_cache = json.load(f)
                tmdb_cache_entries = len(tmdb_cache)
        except Exception:
            pass

    # Cache Letterboxd
    lb_cache_entries = 0
    lb_cache_size = "0 B"
    lb_path = os.path.join(RAW_DIR, "letterboxd_raw_responses.json")
    if os.path.exists(lb_path):
        lb_cache_size = get_file_size_str(lb_path)
        try:
            with open(lb_path, "r", encoding="utf-8") as f:
                lb_cache = json.load(f)
                lb_cache_entries = len(lb_cache)
        except Exception:
            pass
            
    # Total de lignes unifiées dans la DB
    unified_total_rows = 0
    if os.path.exists(DB_PATH):
        try:
            conn = get_db_connection()
            unified_total_rows = conn.execute("SELECT COUNT(*) FROM Fact_Films").fetchone()[0]
            conn.close()
        except Exception:
            pass

    return jsonify({
        "raw_csv_count": csv_count,
        "raw_csv_size": csv_size_str,
        "raw_total_rows": raw_total_rows,
        "tmdb_cache_entries": tmdb_cache_entries,
        "tmdb_cache_size": tmdb_cache_size,
        "letterboxd_cache_entries": lb_cache_entries,
        "letterboxd_cache_size": lb_cache_size,
        "unified_total_rows": unified_total_rows
    })

@app.route('/api/titles')
def get_titles():
    """
    Explorateur de base de données paginé et filtré.
    """
    page = int(request.args.get('page', 1))
    search = request.args.get('search', '').strip()
    platform = request.args.get('platform', '').strip()
    genre = request.args.get('genre', '').strip()
    
    limit = 50
    offset = (page - 1) * limit
    
    if not os.path.exists(DB_PATH):
        return jsonify({"titles": [], "page": 1, "total_pages": 0, "total_count": 0})
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Construire la requête SQL avec filtres
    where_clauses = []
    params = {}
    
    # 1. Filtre par recherche textuelle (titre)
    if search:
        where_clauses.append("f.titre LIKE :search")
        params["search"] = f"%{search}%"
        
    # 2. Filtre par plateforme
    if platform:
        if platform == "netflix":
            where_clauses.append("""
                f.film_id IN (
                    SELECT film_id FROM Lien_Film_Plateforme l 
                    JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
                    WHERE p.nom = 'Netflix'
                )
            """)
        elif platform == "amazon":
            where_clauses.append("""
                f.film_id IN (
                    SELECT film_id FROM Lien_Film_Plateforme l 
                    JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
                    WHERE p.nom = 'Prime Video'
                )
            """)
        elif platform == "disney":
            where_clauses.append("""
                f.film_id IN (
                    SELECT film_id FROM Lien_Film_Plateforme l 
                    JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
                    WHERE p.nom = 'Disney+'
                )
            """)
            
    # 3. Filtre par genre
    if genre:
        where_clauses.append("""
            f.film_id IN (
                SELECT film_id FROM Lien_Film_Genre lg 
                JOIN Dim_Genres g ON lg.genre_id = g.genre_id 
                WHERE g.nom = :genre
            )
        """)
        params["genre"] = genre
        
    # Assembler le WHERE
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)
        
    # Compter le total de résultats correspondants
    count_sql = f"SELECT COUNT(*) FROM Fact_Films f {where_sql}"
    total_count = cursor.execute(count_sql, params).fetchone()[0]
    total_pages = (total_count + limit - 1) // limit
    
    # Exécuter la requête principale avec pagination
    main_sql = f"""
        SELECT f.film_id, f.titre as title, f.type as type, f.budget as tmdb_budget, f.revenu_box_office as tmdb_revenue, 
               f.note_moyenne as tmdb_rating, f.date_sortie as release_year, f.note_letterboxd as letterboxd_rating,
               f.votes_letterboxd as letterboxd_vote_count, f.fans_letterboxd as letterboxd_fans_count,
               f.imdb_id, f.rentabilite as rentability, f.benefice as profit,
               EXISTS (
                   SELECT 1 FROM Lien_Film_Plateforme l 
                   JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
                   WHERE l.film_id = f.film_id AND p.nom = 'Netflix'
               ) as on_netflix,
               EXISTS (
                   SELECT 1 FROM Lien_Film_Plateforme l 
                   JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
                   WHERE l.film_id = f.film_id AND p.nom = 'Prime Video'
               ) as on_amazon_prime,
               EXISTS (
                   SELECT 1 FROM Lien_Film_Plateforme l 
                   JOIN Dim_Plateformes p ON l.plateforme_id = p.plateforme_id 
                   WHERE l.film_id = f.film_id AND p.nom = 'Disney+'
               ) as on_disney_plus
        FROM Fact_Films f
        {where_sql}
        ORDER BY f.titre ASC
        LIMIT :limit OFFSET :offset
    """
    
    params["limit"] = limit
    params["offset"] = offset
    
    rows = cursor.execute(main_sql, params).fetchall()
    
    titles_list = []
    for row in rows:
        titles_list.append(dict(row))
        
    conn.close()
    
    return jsonify({
        "titles": titles_list,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count
    })

if __name__ == "__main__":
    print("[INFO] Démarrage du serveur Flask de supervision...")
    print("[INFO] Ouvrez votre navigateur sur http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
