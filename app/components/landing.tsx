import { LandingNavLink, LandingPrimaryCta } from "./landing-auth";

export default function Landing() {
  return (
    <div className="landing">
      <div className="landingField" aria-hidden="true">
        <span className="landingGrid" />
      </div>
      <div className="vignette" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />

      <header className="landingTop">
        <a className="landingWordmark" href="#top" aria-label="Auren home">
          Auren
        </a>
        <div className="landingNav">
          <span className="privacyLabel">
            <span aria-hidden="true" />
            Private by design
          </span>
          <LandingNavLink />
        </div>
      </header>

      <main id="top">
        <section className="landingHero">
          <div className="landingCopy">
            <p className="landingEyebrow">Personal voice intelligence</p>
            <h1 className="landingHeadline">
              A quieter way to get things <em>done.</em>
            </h1>
            <p className="landingLine">
              Talk naturally. Auren remembers what matters with your consent,
              handles useful work, and stays out of the way until you need it.
            </p>
            <div className="landingCta">
              <LandingPrimaryCta />
              <span className="landingCtaNote">Voice and text, whenever you prefer</span>
            </div>
            <ul className="landingPrinciples" aria-label="Auren principles">
              <li>Consent-led memory</li>
              <li>Reviewable actions</li>
              <li>Calm by default</li>
            </ul>
          </div>

          <div className="landingVisual" aria-hidden="true">
            <span className="visualHalo visualHaloOuter" />
            <span className="visualHalo visualHaloInner" />
            <span className="visualOrbit visualOrbitA" />
            <span className="visualOrbit visualOrbitB" />
            <span className="visualCore" />
          </div>
        </section>

        <section className="capabilityGrid" aria-label="What Auren can do">
          <article>
            <span>01</span>
            <h2>Speak naturally</h2>
            <p>A responsive voice workspace that leaves room for thought.</p>
          </article>
          <article>
            <span>02</span>
            <h2>Keep useful context</h2>
            <p>See, shape, and remove the personal details Auren remembers.</p>
          </article>
          <article>
            <span>03</span>
            <h2>Act with confidence</h2>
            <p>Consequential actions remain visible and require your approval.</p>
          </article>
        </section>
      </main>

      <footer className="landingFooter">
        <span>Auren</span>
        <p>Intelligence that feels present, not intrusive.</p>
      </footer>
    </div>
  );
}
