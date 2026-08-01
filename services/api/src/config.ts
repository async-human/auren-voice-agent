import { z } from "zod";
import dotenv from "dotenv";

if (process.env.AUREN_ENV !== "production") {
  dotenv.config({ path: ".env.local" });
}

const schema = z.object({
  AUREN_ENV: z.enum(["development", "staging", "production"]).default("development"),
  PORT: z.coerce.number().int().positive().default(8080),
  CORS_ORIGINS: z.string().min(1),
  LIVEKIT_URL: z.string().url().refine((value) => value.startsWith("wss://"), {
    message: "LIVEKIT_URL must use wss://",
  }),
  LIVEKIT_API_KEY: z.string().min(1),
  LIVEKIT_API_SECRET: z.string().min(1),
  LIVEKIT_AGENT_NAME: z.string().min(1).default("auren-agent"),
});

const result = schema.safeParse(process.env);
if (!result.success) {
  console.error("Invalid runtime configuration", result.error.flatten().fieldErrors);
  process.exit(1);
}

export const config = {
  ...result.data,
  corsOrigins: result.data.CORS_ORIGINS.split(",").map((origin) => origin.trim()),
};
