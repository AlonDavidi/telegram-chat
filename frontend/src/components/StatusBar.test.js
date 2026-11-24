import { expect, test } from "vitest";
import { participantLabel } from "./StatusBar";

test("no participant → waiting", () => {
  expect(participantLabel({ connected: false, session_id: "s1", participant: null })).toBe(
    "Waiting for a Telegram user to send /start",
  );
});

test("username shown with @", () => {
  expect(
    participantLabel({ connected: true, session_id: "s1", participant: { first_name: "Dana", username: "dana" } }),
  ).toBe("Connected to @dana");
});

test("no username falls back to first name (never @null)", () => {
  expect(
    participantLabel({ connected: true, session_id: "s1", participant: { first_name: "Dana", username: null } }),
  ).toBe("Connected to Dana");
});
