"use client";

import Link from "next/link";
import { SignInButton, useAuth } from "@clerk/nextjs";

export default function Landing() {
  const { isLoaded, isSignedIn } = useAuth();

  return (
    <div className="landing">
      <div className="landingField" aria-hidden="true">
        <span className="landingOrb landingOrbA" />
        <span className="landingOrb landingOrbB" />
        <span className="landingOrb landingOrbC" />
        <span className="landingRing" />
      </div>
      <div className="vignette" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />

      <header className="landingTop">
        <div className="who">
          <span className="mark" />
          <span className="whoName">
            Auren
            <small>Private voice intelligence</small>
          </span>
        </div>
        {isSignedIn ? (
          <Link className="leave" href="/talk">
            Open studio
          </Link>
        ) : (
          <SignInButton mode="modal" forceRedirectUrl="/talk" fallbackRedirectUrl="/talk">
            <button type="button" className="leave" disabled={!isLoaded}>
              Sign in
            </button>
          </SignInButton>
        )}
      </header>

      <main className="landingHero">
        <p className="landingEyebrow">A personal voice companion</p>
        <h1 className="landingBrand">
          <span>Auren</span>
        </h1>
        <p className="landingLine">
          Speaks with you, remembers with your consent, and stays out of the way
          until you need it.
        </p>

        <div className="landingCta">
          {isSignedIn ? (
            <Link className="landingPrimary" href="/talk">
              <span className="pip" />
              <span>Enter the studio</span>
            </Link>
          ) : (
            <SignInButton mode="modal" forceRedirectUrl="/talk" fallbackRedirectUrl="/talk">
              <button type="button" className="landingPrimary" disabled={!isLoaded}>
                <span className="pip" />
                <span>{isLoaded ? "Sign in to talk" : "Loading…"}</span>
              </button>
            </SignInButton>
          )}
        </div>
      </main>

      <section className="landingNote" aria-label="How Auren treats memory">
        <h2>Memory, not surveillance</h2>
        <p>
          Auren keeps only what you ask it to remember, shows you the list, and
          forgets on command.
        </p>
      </section>
    </div>
  );
}
