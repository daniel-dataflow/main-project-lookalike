# spark/utils/postgres_connector.py
import psycopg2

def get_postgres_conn():
    return psycopg2.connect(
        host="localhost",
        dbname="datadb",
        user="datauser",
        password="DataPass2024!"
    )