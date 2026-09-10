FROM eagleeye-media-demo:20260910
RUN pip install --no-cache-dir supervision==0.30.2 \
    && pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torchvision==0.29.0+cpu
COPY runner.py server.py install_vision.py /demo/
ENTRYPOINT ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8020", "--workers", "1"]
