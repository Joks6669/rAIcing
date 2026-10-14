# rAIcing

Application web construite avec Next.js (App Router, TypeScript, Tailwind CSS), une API REST
dans `src/app/api` et une base PostgreSQL gérée par Prisma.

## Structure

```
prisma/
  schema.prisma        Modèles de données
  migrations/          Migrations SQL versionnées
  seed.ts              Données de démonstration
src/
  app/                 Pages (App Router)
  app/api/             Routes d'API
  lib/db.ts            Client Prisma partagé
  lib/validation.ts    Schémas de validation (zod)
docker-compose.yml     PostgreSQL local
```

## Démarrage

Prérequis : Node.js 20+ et Docker.

```bash
cp .env.example .env
npm install
npm run db:up        # lance PostgreSQL en local
npm run db:migrate   # applique les migrations
npm run db:seed      # (facultatif) données de démonstration
npm run dev          # http://localhost:3000
```

## API

| Méthode | Route             | Description               |
| ------- | ----------------- | ------------------------- |
| GET     | `/api/health`     | État de l'app et de la base |
| GET     | `/api/items`      | Liste des éléments        |
| POST    | `/api/items`      | Crée un élément           |
| GET     | `/api/items/:id`  | Détail d'un élément       |
| PATCH   | `/api/items/:id`  | Modifie un élément        |
| DELETE  | `/api/items/:id`  | Supprime un élément       |

Le modèle `Item` sert d'exemple : il est à remplacer par les entités métier du projet.

## Scripts utiles

- `npm run lint` / `npm run typecheck` / `npm run build`
- `npm run db:studio` : interface d'exploration de la base
- `npm run db:deploy` : applique les migrations en production
