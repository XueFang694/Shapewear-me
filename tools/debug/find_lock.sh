#!/bin/bash
# Trouve quel processus verrouille shapewear.db
# Lance ce script dans un terminal PENDANT que le lock se produit

DB_PATH="$(dirname "$0")/data/shapewear.db"
echo "=== Recherche du processus qui verrouille ==="
echo "Fichier : $DB_PATH"
echo ""

# lsof : liste les processus qui ont le fichier ouvert
echo "--- lsof ---"
lsof "$DB_PATH" 2>/dev/null || echo "(lsof non disponible)"
echo ""

# fuser : plus direct
echo "--- fuser ---"
fuser -v "$DB_PATH" 2>/dev/null || echo "(fuser non disponible)"
echo ""

# Fichiers WAL présents ?
echo "--- Fichiers SQLite ---"
ls -lh "$(dirname "$DB_PATH")/shapewear"* 2>/dev/null
echo ""

# Processus Python en cours
echo "--- Processus Python actifs ---"
ps aux | grep -E "python|shapewear|sqlite" | grep -v grep