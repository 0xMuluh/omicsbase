FROM omicsbase-backend:dev

USER root
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --break-system-packages -r /app/requirements.txt

COPY . /app/engine

ENV PYTHONPATH=/app
ENV PROJECTS_DIR=/app/projects
ENV RECIPES_DIR=/app/recipes
EXPOSE 8001

CMD ["python3", "-m", "engine.server"]
