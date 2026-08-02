# Development image for the SPOP React PWA.
# Runs the Vite dev server with hot-module-reload via a bind mount.
# A separate multi-stage Dockerfile (build + nginx-serve) should replace
# this before deploying beyond local/dev environments.
FROM node:22-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
