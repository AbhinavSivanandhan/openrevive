import { NextRequest, NextResponse } from "next/server";

const AUTH_REALM = "OpenRevive";

type Credentials = {
  username: string;
  password: string;
};

function authEnabled(): boolean {
  return process.env.BASIC_AUTH_ENABLED === "true";
}

function configuredCredentials(): Credentials[] {
  const credentials: Credentials[] = [];

  const username = process.env.BASIC_AUTH_USERNAME;
  const password = process.env.BASIC_AUTH_PASSWORD;

  if (username && password) {
    credentials.push({ username, password });
  }

  const secondUsername = process.env.BASIC_AUTH_USERNAME_2;
  const secondPassword = process.env.BASIC_AUTH_PASSWORD_2;

  if (secondUsername && secondPassword) {
    credentials.push({
      username: secondUsername,
      password: secondPassword,
    });
  }

  return credentials;
}

function unauthorized(): NextResponse {
  return new NextResponse("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": (
        `Basic realm="${AUTH_REALM}", charset="UTF-8"`
      ),
    },
  });
}

function parseAuthorization(
  authorization: string | null,
): Credentials | null {
  if (!authorization) {
    return null;
  }

  const [scheme, encoded] = authorization.split(" ", 2);

  if (scheme?.toLowerCase() !== "basic" || !encoded) {
    return null;
  }

  try {
    const decoded = atob(encoded);
    const separator = decoded.indexOf(":");

    if (separator < 0) {
      return null;
    }

    return {
      username: decoded.slice(0, separator),
      password: decoded.slice(separator + 1),
    };
  } catch {
    return null;
  }
}

function isAuthorized(
  authorization: string | null,
  credentials: Credentials[],
): boolean {
  const supplied = parseAuthorization(authorization);

  if (!supplied) {
    return false;
  }

  return credentials.some(
    (expected) =>
      supplied.username === expected.username &&
      supplied.password === expected.password,
  );
}

export function proxy(request: NextRequest): NextResponse {
  if (!authEnabled()) {
    return NextResponse.next();
  }

  const credentials = configuredCredentials();

  if (credentials.length === 0) {
    return new NextResponse(
      "Authentication is enabled but not configured.",
      { status: 503 },
    );
  }

  if (
    isAuthorized(
      request.headers.get("authorization"),
      credentials,
    )
  ) {
    return NextResponse.next();
  }

  return unauthorized();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml).*)",
  ],
};
