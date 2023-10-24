FROM python:3.11

ENV PYTHONUNBUFFERED 1
ENV PIPENV_VENV_IN_PROJECT 1

WORKDIR /app

COPY Pipfile Pipfile.lock /app/

RUN pip install --no-cache-dir pipenv && \
    pipenv install --deploy --system
#Copy the rest of the application code

COPY . /app/
#Set the entrypoint command

CMD ["python", "main.py"]