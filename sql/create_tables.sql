-- Script SQL de création des tables pour le schéma en étoile (SQLite)
PRAGMA foreign_keys = OFF;
DROP TABLE IF EXISTS Lien_Film_Genre;
DROP TABLE IF EXISTS Lien_Film_Plateforme;
DROP TABLE IF EXISTS Dim_Genres;
DROP TABLE IF EXISTS Dim_Plateformes;
DROP TABLE IF EXISTS Fact_Films;
PRAGMA foreign_keys = ON;

-- Table des Faits : Fact_Films
CREATE TABLE IF NOT EXISTS Fact_Films (
    film_id TEXT PRIMARY KEY,
    titre TEXT NOT NULL,
    type TEXT,
    budget REAL,
    revenu_box_office REAL,
    note_moyenne REAL, -- Note moyenne TMDB
    date_sortie TEXT,
    imdb_id TEXT,
    note_letterboxd REAL,
    votes_letterboxd INTEGER,
    fans_letterboxd INTEGER,
    rentabilite REAL,
    benefice REAL
);

-- Table de Dimension : Dim_Plateformes
CREATE TABLE IF NOT EXISTS Dim_Plateformes (
    plateforme_id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT UNIQUE NOT NULL
);

-- Table de Dimension : Dim_Genres
CREATE TABLE IF NOT EXISTS Dim_Genres (
    genre_id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT UNIQUE NOT NULL
);

-- Table de Liaison (Many-to-Many) : Lien_Film_Plateforme
CREATE TABLE IF NOT EXISTS Lien_Film_Plateforme (
    film_id TEXT,
    plateforme_id INTEGER,
    date_ajout_catalogue TEXT,
    PRIMARY KEY (film_id, plateforme_id),
    FOREIGN KEY (film_id) REFERENCES Fact_Films (film_id) ON DELETE CASCADE,
    FOREIGN KEY (plateforme_id) REFERENCES Dim_Plateformes (plateforme_id) ON DELETE CASCADE
);

-- Table de Liaison (Many-to-Many) : Lien_Film_Genre
CREATE TABLE IF NOT EXISTS Lien_Film_Genre (
    film_id TEXT,
    genre_id INTEGER,
    PRIMARY KEY (film_id, genre_id),
    FOREIGN KEY (film_id) REFERENCES Fact_Films (film_id) ON DELETE CASCADE,
    FOREIGN KEY (genre_id) REFERENCES Dim_Genres (genre_id) ON DELETE CASCADE
);
