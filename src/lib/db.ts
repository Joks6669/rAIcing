import { PrismaClient } from "@prisma/client";

// Réutilise une seule instance en développement pour éviter
// d'ouvrir une connexion à chaque rechargement à chaud.
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== "production") globalForPrisma.prisma = prisma;
