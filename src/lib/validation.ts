import { z } from "zod";

export const itemInput = z.object({
  title: z.string().trim().min(1).max(200),
  description: z.string().trim().max(2000).optional(),
});

export const itemUpdate = itemInput.partial();
