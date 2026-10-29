import { firebaseAuth } from "./firebase";

export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  const user = firebaseAuth?.currentUser;
  if (!user) {
    throw new Error("Your session has ended. Sign in again to continue.");
  }
  headers.set("Authorization", `Bearer ${await user.getIdToken()}`);
  return window.fetch(input, { ...init, headers });
}
