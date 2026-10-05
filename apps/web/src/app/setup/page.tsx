import { demoSetup, demoTags } from "@/lib/demo-match";

export default function SetupPage() {
  const tags = demoTags();
  const home = tags.filter((t) => t.panel === "home_attacking");
  const away = tags.filter((t) => t.panel === "away_defending");
  const shared = tags.filter((t) => t.panel === "shared");
  return (
    <>
      <h1>Match setup</h1>
      <p className="lead">
        Teams, kits, attack direction, line-ups, and video. Demo match is prefilled
        (Pisa vs Perugia) while the DB-backed form ships next.
      </p>
      <div className="grid">
        <section className="card">
          <h2>Fixture</h2>
          <label>Home team</label>
          <input defaultValue={demoSetup.homeTeam} readOnly />
          <label>Away team</label>
          <input defaultValue={demoSetup.awayTeam} readOnly />
          <label>Home kit</label>
          <input defaultValue={demoSetup.homeKitColor} readOnly />
          <label>Away kit</label>
          <input defaultValue={demoSetup.awayKitColor} readOnly />
          <label>Home attacks (1H)</label>
          <input
            defaultValue={
              demoSetup.homeAttacksLeftToRightFirstHalf ? "Left → right" : "Right → left"
            }
            readOnly
          />
        </section>
        <section className="card">
          <h2>Line-ups</h2>
          <table>
            <thead>
              <tr>
                <th>Side</th>
                <th>#</th>
                <th>Player</th>
                <th>Pos</th>
              </tr>
            </thead>
            <tbody>
              {demoSetup.lineups.map((p) => (
                <tr key={p.id}>
                  <td>{p.side}</td>
                  <td>{p.number ?? "—"}</td>
                  <td>{p.name}</td>
                  <td>{p.position ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <div className="grid" style={{ marginTop: "1rem" }}>
        <section className="card">
          <h2>Home / Attacking (green) · {home.length}</h2>
          <ul className="muted">
            {home.map((t) => (
              <li key={t.id}>
                <strong>{t.onceSportName}</strong>
              </li>
            ))}
          </ul>
        </section>
        <section className="card">
          <h2>Away / Defending (orange) · {away.length}</h2>
          <ul className="muted">
            {away.map((t) => (
              <li key={t.id}>
                <strong>{t.onceSportName}</strong>
              </li>
            ))}
          </ul>
        </section>
        <section className="card">
          <h2>Other (shared) · {shared.length}</h2>
          <ul className="muted">
            {shared.map((t) => (
              <li key={t.id}>
                <strong>{t.onceSportName}</strong>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </>
  );
}
