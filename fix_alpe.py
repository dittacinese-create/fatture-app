import psycopg2

DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Ripristina l'importo corretto della fattura 37 (Alpe)
cur.execute("""
    UPDATE fatture 
    SET totale = 8756.00, regime_iva = 'RC' 
    WHERE numero = '37';
""")

conn.commit()
cur.close()
conn.close()
print("✅ Fattura 37 di Alpe ripristinata a € 8.756,00 con successo!")