/**
 * Backend URL resolution.
 *
 * Defaults to http://localhost:8000 for local dev. Override at build time
 * with NEXT_PUBLIC_API_URL.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const CHAT_ENDPOINT = `${API_URL}/api/chat`;

export function stateEndpoint(conversationId: string): string {
  return `${API_URL}/api/session/${conversationId}/state`;
}

export function reportEndpoint(conversationId: string): string {
  return `${API_URL}/api/session/${conversationId}/report`;
}
