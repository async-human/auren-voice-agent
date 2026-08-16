import { LandingNavLink, LandingPrimaryCta } from "./landing-auth";

export default function Landing() {
  return (
    <div className="landing">
      <div className="landingField" aria-hidden="true">
        <span className="landingGrid" />
        <span className="landingGlow landingGlowA" />
        <span className="landingGlow landingGlowB" />
      </div>
      <div className="vignette" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />

      <header className="landingTop">
        <a className="landingWordmark" href="#top" aria-label="Auren home">
          <span className="brandGlyph" aria-hidden="true"><i /></span>
          <span>Auren</span>
        </a>
        <div className="landingNav">
          <span className="privacyLabel">
            <span aria-hidden="true" />
            Private presence
          </span>
          <LandingNavLink />
        </div>
      </header>

      <main id="top">
        <section className="landingHero">
          <div className="landingCopy">
            <p className="landingEyebrow">
              <span aria-hidden="true" />
              Personal voice intelligence
            </p>
            <h1 className="landingHeadline">
              Intelligence<br />
              that feels <em>present.</em>
            </h1>
            <p className="landingLine">
              Meet Auren — a sharp, dependable operator that listens, remembers with
              permission, and turns conversation into completed action.
            </p>
            <div className="landingCta">
              <LandingPrimaryCta />
              <span className="landingCtaNote">
                <i aria-hidden="true" />
                Ready when you are
              </span>
            </div>
            <ul className="landingPrinciples" aria-label="Auren principles">
              <li><strong>Natural</strong><span>Talk without commands</span></li>
              <li><strong>Personal</strong><span>Memory you control</span></li>
              <li><strong>Capable</strong><span>Actions you approve</span></li>
            </ul>
          </div>

          <div className="landingVisual" aria-hidden="true">
            <div className="livingPresence">
              <span className="visualHalo visualHaloOuter" />
              <span className="visualHalo visualHaloMiddle" />
              <span className="visualOrbit visualOrbitA" />
              <span className="visualOrbit visualOrbitB" />
              <span className="visualMembrane">
                <i className="membraneLight" />
                <i className="membraneShadow" />
              </span>
              <span className="visualCore" />
              <span className="visualParticle particleA" />
              <span className="visualParticle particleB" />
              <span className="visualParticle particleC" />
            </div>
            <div className="visualSignal">
              <span /><span /><span /><span /><span /><span /><span />
            </div>
            <p>Listening between the words</p>
          </div>
        </section>

        <section className="capabilityGrid" aria-label="What Auren can do">
          <article>
            <span className="capabilityIndex">01</span>
            <div>
              <h2>Understands your rhythm</h2>
              <p>Natural turn-taking, gentle interruptions, and room to think.</p>
            </div>
          </article>
          <article>
            <span className="capabilityIndex">02</span>
            <div>
              <h2>Grows more useful</h2>
              <p>Context carries forward only when you want it to.</p>
            </div>
          </article>
          <article>
            <span className="capabilityIndex">03</span>
            <div>
              <h2>Moves work forward</h2>
              <p>From thought to action, with you in control at every step.</p>
            </div>
          </article>
        </section>
      </main>

      <footer className="landingFooter">
        <span>Human pace. Machine capability.</span>
        <p>Built around your voice, privacy, and intent.</p>
      </footer>
    </div>
  );
}
