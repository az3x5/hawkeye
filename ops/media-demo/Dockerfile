FROM eagleeye-media-demo:20260910
COPY runner.py server.py /demo/
ENTRYPOINT ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8020", "--workers", "1"]
