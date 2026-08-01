import crypto from "node:crypto";

import { RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";
import cors from "cors";
import express from "express";
import { rateLimit } from "express-rate-limit";
import helmet from "helmet";
import { AccessToken } from "livekit-server-sdk";

import { config } from "./config.js";

const app = express();
app.set("trust proxy", 1);
app.disable("x-powered-by");
app.use(helmet());
app.use(express.json({ limit: "16kb" }));
app.use(cors({ origin: config.corsOrigins, methods: ["GET", "POST"] }));

app.get("/health", (_request, response) => {
  response.json({ status: "ok", environment: config.AUREN_ENV });
});

app.post(
  "/v1/voice/token",
  rateLimit({ windowMs: 60_000, limit: 10, standardHeaders: "draft-8" }),
  async (_request, response, next) => {
    try {
      const nonce = crypto.randomUUID();
      const roomName = `auren-${nonce}`;
      const userId = `anonymous-${nonce}`;
      const token = new AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET, {
        identity: `user-${nonce}`,
        name: "Auren user",
        metadata: JSON.stringify({ userId }),
        ttl: "10m",
      });

      token.addGrant({
        room: roomName,
        roomJoin: true,
        canPublish: true,
        canSubscribe: true,
        canPublishData: true,
      });
      token.roomConfig = new RoomConfiguration({
        agents: [
          new RoomAgentDispatch({
            agentName: config.LIVEKIT_AGENT_NAME,
            metadata: JSON.stringify({ source: "auren-web" }),
          }),
        ],
      });

      response.setHeader("Cache-Control", "no-store");
      response.json({
        serverUrl: config.LIVEKIT_URL,
        participantToken: await token.toJwt(),
      });
    } catch (error) {
      next(error);
    }
  },
);

app.use((error: unknown, _request: express.Request, response: express.Response, _next: express.NextFunction) => {
  console.error("Unhandled API error", error);
  response.status(500).json({ error: "Internal server error" });
});

app.listen(config.PORT, "0.0.0.0", () => {
  console.log(`Auren API listening on port ${config.PORT}`);
});
