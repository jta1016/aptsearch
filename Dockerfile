FROM apify/actor-python-playwright:3.11

COPY requirements.txt ./
RUN pip install --upgrade -r requirements.txt

COPY src/ ./src/

CMD ["python", "src/main.py"]
