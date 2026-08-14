"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";

export function LandingNavLink() {
  const { isLoaded, isSignedIn } = useAuth();

  if (isSignedIn) {
    return (
      <Link className="leave" href="/talk">
        Open June
      </Link>
    );
  }

  return (
    <Link
      className={`leave ${!isLoaded ? "disabledLink" : ""}`}
      href={isLoaded ? "/sign-in" : "#"}
      aria-disabled={!isLoaded}
    >
      Sign in
    </Link>
  );
}

export function LandingPrimaryCta() {
  const { isLoaded, isSignedIn } = useAuth();

  if (isSignedIn) {
    return (
      <Link className="landingPrimary" href="/talk">
        <span>Start a conversation</span>
        <span className="ctaArrow" aria-hidden="true">↗</span>
      </Link>
    );
  }

  return (
    <Link
      className={`landingPrimary ${!isLoaded ? "disabledLink" : ""}`}
      href={isLoaded ? "/sign-in" : "#"}
      aria-disabled={!isLoaded}
    >
      <span>Sign in to start</span>
      <span className="ctaArrow" aria-hidden="true">↗</span>
    </Link>
  );
}
