#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script Maître (Orchestrateur) du Pipeline ETL.
Ce script orchestre l'exécution de bout en bout :
1. Extraction : Récupération des données TMDB et Letterboxd (via CLI).
2. Transformation : Nettoyage, Fuzzy Matching et enrichissement.
3. Chargement : Injection dans la base de données relationnelle SQLite.

Utilisation :
  python main.py --demo (Mode démonstration rapide)
  python main.py --all (Traitement complet)
"""

import os
import sys
import subprocess
import argparse

def get_python_interpreter():
    """
    Retourne le chemin de l'interpréteur Python dans le dossier .venv s'il existe.
    Sinon, retourne l'interpréteur actuel (sys.executable).
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Chemins potentiels de l'interpréteur dans le venv
    venv_paths = [
        os.path.join(base_dir, ".venv", "Scripts", "python.exe"), # Windows
        os.path.join(base_dir, ".venv", "bin", "python"),        # Unix
        os.path.join(base_dir, "venv", "Scripts", "python.exe"),  # Windows (alt venv)
        os.path.join(base_dir, "venv", "bin", "python")           # Unix (alt venv)
    ]
    
    for path in venv_paths:
        if os.path.exists(path):
            print(f"[PIPELINE] Environnement virtuel local détecté : {path}")
            return path
            
    print(f"[PIPELINE] [WARNING] Aucun environnement virtuel local trouvé. Utilisation de l'interpréteur actuel.")
    return sys.executable

def run_step(command_list, step_name, python_interpreter):
    """
    Exécute une commande système pour une étape du pipeline et gère le retour.
    """
    print(f"\n==================================================")
    print(f"[PIPELINE] Démarrage de l'étape : {step_name}")
    print(f"Commande : {python_interpreter} {' '.join(command_list)}")
    print(f"==================================================")
    
    try:
        result = subprocess.run(
            [python_interpreter] + command_list,
            check=True
        )
        print(f"[PIPELINE] Étape '{step_name}' terminée avec succès.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[PIPELINE] [ERROR] Échec lors de l'étape '{step_name}'. Code de sortie : {e.returncode}")
        return False
    except Exception as e:
        print(f"[PIPELINE] [ERROR] Erreur imprévue lors de l'étape '{step_name}' : {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Orchestrateur global du pipeline ETL.")
    
    # Options générales
    parser.add_argument("--demo", action="store_true", help="Lance le pipeline en mode démo (limite à 5 requêtes API TMDB et Letterboxd).")
    parser.add_argument("--all", action="store_true", help="Lance le pipeline complet sans limite (attention aux quotas API TMDB et au scraping Letterboxd).")
    
    # Options fines par étape
    parser.add_argument("--skip-extract", action="store_true", help="Passe l'étape d'extraction et utilise les caches existants.")
    parser.add_argument("--skip-transform", action="store_true", help="Passe l'étape de transformation.")
    parser.add_argument("--skip-load", action="store_true", help="Passe l'étape de chargement en base de données.")
    
    # Options de configuration de l'extraction
    parser.add_argument("--limit-tmdb", type=int, default=10, help="Nombre max de titres à extraire de TMDB (défaut : 10).")
    parser.add_argument("--limit-letterboxd", type=int, default=5, help="Nombre max de films à scraper de Letterboxd (défaut : 5).")
    
    # Option Dashboard
    parser.add_argument("--serve", action="store_true", help="Lance le serveur Flask de supervision et d'exploration de la base de données après le pipeline.")
    
    args = parser.parse_args()

    print("==================================================")
    print("      INITIALISATION DU PIPELINE ETL COMPLET      ")
    print("==================================================")

    # Déterminer les chemins absolus
    base_dir = os.path.dirname(os.path.abspath(__file__))
    extract_tmdb_script = os.path.join(base_dir, "scripts", "extract", "extract_tmdb.py")
    extract_letterboxd_script = os.path.join(base_dir, "scripts", "extract", "extract_letterboxd.py")
    transform_script = os.path.join(base_dir, "scripts", "transform", "transform_data.py")
    load_script = os.path.join(base_dir, "scripts", "load", "load_db.py")

    # Déterminer l'interpréteur Python du venv
    python_bin = get_python_interpreter()

    # 1. ÉTAPE 1 : EXTRACTION
    if not args.skip_extract:
        # Configurer les limites d'extraction
        if args.demo:
            tmdb_limit = 5
            lb_limit = 3
            tmdb_args = ["--limit", str(tmdb_limit)]
            lb_args = ["--limit", str(lb_limit)]
        elif args.all:
            tmdb_args = ["--all"]
            lb_args = ["--all"]
        else:
            tmdb_args = ["--limit", str(args.limit_tmdb)]
            lb_args = ["--limit", str(args.limit_letterboxd)]
            
        # A. Extraire TMDB
        success_tmdb = run_step([extract_tmdb_script] + tmdb_args, "Extraction API TMDB", python_bin)
        if not success_tmdb:
            print("[PIPELINE] [WARNING] Échec de l'extraction TMDB. Poursuite du pipeline avec les caches existants...")
            
        # B. Extraire Letterboxd
        success_lb = run_step([extract_letterboxd_script] + lb_args, "Scraping Web Letterboxd", python_bin)
        if not success_lb:
            print("[PIPELINE] [WARNING] Échec du scraping Letterboxd. Poursuite du pipeline avec les caches existants...")
    else:
        print("\n[PIPELINE] Étape d'extraction ignorée (--skip-extract).")

    # 2. ÉTAPE 2 : TRANSFORMATION
    if not args.skip_transform:
        success_trans = run_step([transform_script], "Transformation & Fuzzy Matching", python_bin)
        if not success_trans:
            print("[PIPELINE] [ERROR] Échec de la transformation. Arrêt du pipeline.")
            sys.exit(1)
    else:
        print("\n[PIPELINE] Étape de transformation ignorée (--skip-transform).")

    # 3. ÉTAPE 3 : CHARGEMENT (LOAD)
    if not args.skip_load:
        success_load = run_step([load_script], "Chargement Base SQL (SQLAlchemy)", python_bin)
        if not success_load:
            print("[PIPELINE] [ERROR] Échec du chargement en base. Arrêt du pipeline.")
            sys.exit(1)
    else:
        print("\n[PIPELINE] Étape de chargement ignorée (--skip-load).")

    print("\n==================================================")
    print("[SUCCESS] EXECUTION DU PIPELINE TERMINEE AVEC SUCCES")
    print("==================================================")

    # 4. ÉTAPE 4 : LANCEMENT DU SERVEUR DE VISUALISATION (DASHBOARD)
    if args.serve:
        app_script = os.path.join(base_dir, "app.py")
        run_step([app_script], "Lancement Dashboard Web Flask", python_bin)

if __name__ == "__main__":
    main()
