import psycopg2

DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Ripristino Silvio Benedetto (N. 31 -> € 3.067,93)
cur.execute("UPDATE fatture SET totale = 3067.93 WHERE numero = '31';")

# Ripristino Saxso S.r.l. (N. 30 -> € 43.745,78)
cur.execute("UPDATE fatture SET totale = 43745.78 WHERE numero = '30';")

# Ripristino Alpe (N. 37 -> € 8.756,00)
cur.execute("UPDATE fatture SET totale = 8756.00, regime_iva = 'RC' WHERE numero = '37';")

conn.commit()
cur.close()
conn.close()

print("✅ Totali ripristinati con successo!")