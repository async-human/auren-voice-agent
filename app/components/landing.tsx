import { LandingNavLink, LandingPrimaryCta } from "./landing-auth";

export default function Landing() {
  return (
    <div className="landing">
      <div className="landingField" aria-hidden="true">
        <span className="landingOrb landingOrbA" />
        <span className="landingOrb landingOrbB" />
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
        <LandingNavLink />
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
          <LandingPrimaryCta />
        </div>
      </main>

      <section className="landingNote" aria-label="How Auren treats memory">
        <h2>Memory, not surveillance</h2>
        <p>
          Auren keeps useful context within your memory settings, shows you what
          it retained, and forgets on command.
        </p>
      </section>
    </div>
  );
}
