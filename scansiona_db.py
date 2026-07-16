import psycopg2 # o il modulo che usi nel tuo script (es. pg8000, sqlalchemy)

# Sostituisci con le tue reali credenziali di connessione
conn = psycopg2.connect(
    dbname="nome_tuo_database", 
    user="tuo_utente", 
    password="tua_password", 
    host="localhost"
)
cur = conn.cursor()

# Interroghiamo le informazioni di sistema di Postgres
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'fatture';
""")

colonne = cur.fetchall()
print("Ecco le colonne della tabella 'fatture':")
for col in colonne:
    print(f"- {col[0]} ({col[1]})")

cur.close()
conn.close()