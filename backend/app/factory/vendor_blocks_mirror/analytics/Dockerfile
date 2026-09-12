FROM cerebrum-block-base:latest

WORKDIR /app
COPY block.json block.py ./
# run.py is provided by cerebrum-block-base:latest
ENTRYPOINT ["python", "/app/run.py"]
