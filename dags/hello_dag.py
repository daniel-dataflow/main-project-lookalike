from datetime import datetime
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="hello_dag",
    start_date=datetime(2026, 2, 1),
    schedule="@once",
    catchup=False,
):
    BashOperator(task_id="hello", bash_command="echo hello")

