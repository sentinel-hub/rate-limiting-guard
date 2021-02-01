FROM python:3.8-slim as build-syncer
WORKDIR /syncer
RUN \
    apt-get update && \
    apt-get install -q -y git && \
    pip install pipenv
# - prepare pip requirements.txt for backend:
COPY ./Pipfile ./Pipfile.lock /syncer/
RUN pipenv lock -r > /requirements.txt


FROM python:3.8-slim
COPY --from=build-syncer /requirements.txt /requirements.txt
RUN pip install -r /requirements.txt
COPY . /app/
WORKDIR /app
ENTRYPOINT ["python", "syncer.py"]
