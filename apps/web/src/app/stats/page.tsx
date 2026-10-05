import { demoSetup, demoTeamStats } from "@/lib/demo-match";

export default function StatsPage() {
  const { home, away } = demoTeamStats();
  const rows: Array<[string, string | number, string | number]> = [
    ["Goals", home.goals, away.goals],
    ["xG (estimate)", home.xgEstimate, away.xgEstimate],
    ["Shots", home.shots, away.shots],
    ["Shots on target", home.shotsOnTarget, away.shotsOnTarget],
    ["Shot accuracy %", home.shotAccuracy, away.shotAccuracy],
    ["Blocked / missed", `${home.shotsBlocked} / ${home.shotsMissed}`, `${away.shotsBlocked} / ${away.shotsMissed}`],
    ["Inside / outside box", `${home.shotsInsideBox} / ${home.shotsOutsideBox}`, `${away.shotsInsideBox} / ${away.shotsOutsideBox}`],
    ["Headed shots", home.headedShots, away.headedShots],
    ["Possession (estimate) %", home.possessionEstimate, away.possessionEstimate],
    ["Passes (completed)", `${home.passes} (${home.passesCompleted})`, `${away.passes} (${away.passesCompleted})`],
    ["Pass accuracy %", home.passAccuracy, away.passAccuracy],
    ["Key passes", home.keyPasses, away.keyPasses],
    [
      "Progressive completed / attempted",
      `${home.progressivePassesCompleted} / ${home.progressivePassesAttempted}`,
      `${away.progressivePassesCompleted} / ${away.progressivePassesAttempted}`,
    ],
    ["Crosses (accuracy %)", `${home.crosses} (${home.crossAccuracy})`, `${away.crosses} (${away.crossAccuracy})`],
    ["Long balls (accuracy %)", `${home.longBalls} (${home.longBallAccuracy})`, `${away.longBalls} (${away.longBallAccuracy})`],
    ["Through balls (accuracy %)", `${home.throughBalls} (${home.throughBallAccuracy})`, `${away.throughBalls} (${away.throughBallAccuracy})`],
    [
      "Ground duels won",
      `${home.groundDuelsWon}/${home.groundDuelsTotal}`,
      `${away.groundDuelsWon}/${away.groundDuelsTotal}`,
    ],
    [
      "Aerial duels won",
      `${home.aerialDuelsWon}/${home.aerialDuelsTotal}`,
      `${away.aerialDuelsWon}/${away.aerialDuelsTotal}`,
    ],
    ["Interceptions", home.interceptions, away.interceptions],
    ["Recoveries (high)", `${home.recoveries} (${home.recoveriesHigh})`, `${away.recoveries} (${away.recoveriesHigh})`],
    ["Balls lost", home.ballsLost, away.ballsLost],
    ["Saves", home.saves, away.saves],
    ["Fouls committed / won", `${home.foulsCommitted} / ${home.foulsWon}`, `${away.foulsCommitted} / ${away.foulsWon}`],
    ["Yellow / red", `${home.yellowCards} / ${home.redCards}`, `${away.yellowCards} / ${away.redCards}`],
    ["Offsides", home.offsides, away.offsides],
    ["Corners", home.corners, away.corners],
    ["Free kicks / throw-ins", `${home.freeKicks} / ${home.throwIns}`, `${away.freeKicks} / ${away.throwIns}`],
    ["PPDA", home.ppda, away.ppda],
  ];

  return (
    <>
      <h1>
        Team stats: {demoSetup.homeTeam} vs {demoSetup.awayTeam}
      </h1>
      <p className="lead">
        Board aligned to your Grokbot sheet. xG is labelled as an estimate.
      </p>
      <section className="card" style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Stat</th>
              <th>{demoSetup.homeTeam}</th>
              <th>{demoSetup.awayTeam}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, h, a]) => (
              <tr key={label}>
                <td>{label}</td>
                <td>{h}</td>
                <td>{a}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
