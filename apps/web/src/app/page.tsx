import Link from "next/link";

export default function HomePage() {
  return (
    <>
      <h1>STAT MAN</h1>
      <p className="lead">
        Upload a football match. STAT MAN tags the full film in 5-minute blocks,
        lets you review every event, then exports Once Sport Analyser XMLs for
        Home and Away — ready for Once and the stats dashboard.
      </p>
      <div className="grid">
        <section className="card">
          <h2>1. Match setup</h2>
          <p>Teams, kits, attack direction, line-ups, video or URL.</p>
          <Link className="btn" href="/setup">
            Start setup
          </Link>
        </section>
        <section className="card">
          <h2>2. Auto tagging</h2>
          <p>Never skips blocks. Coverage report + re-pass when sparse.</p>
          <Link className="btn secondary" href="/tagging">
            View pipeline
          </Link>
        </section>
        <section className="card">
          <h2>3. Review & export</h2>
          <p>Edit timeline, then download Home.xml and Away.xml.</p>
          <Link className="btn secondary" href="/review">
            Open review
          </Link>
        </section>
        <section className="card">
          <h2>4. Stats board</h2>
          <p>Shots, xG estimate, pass thirds, duels, PPDA, possession.</p>
          <Link className="btn secondary" href="/stats">
            Open stats
          </Link>
        </section>
      </div>
    </>
  );
}
