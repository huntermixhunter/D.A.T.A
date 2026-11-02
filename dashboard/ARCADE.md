# DATA Arcade

Void Patrol is a local, free-roaming space shooter in the spirit of the 1981
arcade game Bosconian, available from **Arcade** in the dashboard navigation.
It shares the Crew Deck's look: the same palette and type, FTL-style hull
segments and shield pips, system power bars, and chamfered panels. The player
ship, the alien fighters, and the six-cannon fortress are the supplied artwork.
There are no accounts, purchases, external game services, or AI calls to play.

## The loop

1. Choose a difficulty on the title screen, then launch a run. Your ship never
   stops flying; you steer it. Green squares on the radar and arrows at the screen
   edge lead to the stations.
2. Each station has six cannon pods that fire from their barrels at a ship in
   front of them. Destroy all six to expose the core, then destroy the core.
   The core hatch also cycles open every few seconds: one shot into the open
   core destroys the whole station at once for a 1,500-point direct hit.
3. Destroy every station to clear the sector, then install one of three system
   upgrades. The ship receives 25 hull repair, full shields, full boost, and more
   pulse charges before the next sector.
4. Every fifth sector is a dreadnought fight instead of stations. See below.
5. Collect salvage cogs and repair drops. Spend banked salvage on permanent hull,
   shield, engine, and weapons upgrades in the hangar between runs.

Pressure rises the longer you stay in a sector. Condition moves from GREEN to
YELLOW after 50 seconds and to RED after 100, with more frequent formation
attacks. A formation is a leader with four wingmen in a V; destroying the leader
(marked with a gold ring) earns a 300-point bonus and scatters the rest. Manta gunships keep
their distance and fire plasma. Shooting a cosmo-mine sets off a blast that
destroys nearby fighters and sets off other mines; flying into one hurts. Asteroids
break apart under fire and block enemy shots. Consecutive kills build a score
multiplier; taking damage or going five seconds without a kill resets it.
Shields recharge after a few seconds without damage.

## Difficulty

Four levels are chosen on the title screen and saved with the profile: Cadet,
Pilot (the default), Veteran, and Ace. Difficulty scales enemy toughness, damage,
fire rate, speed, and numbers, and how quickly later sectors escalate. Veteran
starts one escalation step in and Ace two. The player's ship and hangar upgrades
are identical at every level.

| Level | Enemy toughness | Enemy damage | Fire rate | Escalation per sector | Score | Salvage |
| --- | --- | --- | --- | --- | --- | --- |
| Cadet | x0.8 | x0.7 | x0.75 | x0.7 | x0.75 | x1 |
| Pilot | x1 | x1 | x1 | x1 | x1 | x1 |
| Veteran | x1.15 | x1.25 | x1.25 | x1.4 | x1.5 | x1.25 |
| Ace | x1.3 | x1.45 | x1.5 | x1.75 | x2 | x1.5 |

Sector 1 on Pilot plays exactly as before. After that, every level keeps
escalating instead of leveling off: pods, cores, and fighters toughen faster than
linearly, enemy hits grow 3.5% per step, fighters speed up toward a cap that
boosting still outruns, spawns arrive in larger groups, formations grow to seven
ships, and late gunships fire three-bolt spreads. The pause screen and flight
record show the difficulty.

## Dreadnought

Every fifth sector (5, 10, 15, and so on) replaces the stations with a
dreadnought: the gunship artwork at four times scale, recolored crimson.
Destroying it clears the sector.

- Gun batteries (four on the first dreadnought, six on later ones) track the
  ship and fire cannon rounds. While any battery survives, a violet shield
  deflects hits on the hull. Batteries sit on the outer plates, so attack each
  one from its exposed side.
- The core telegraphs a plasma ring. It brightens while a dashed ring closes in
  for about a second, then slow plasma bolts fire outward with gaps to fly
  through. A pulse clears nearby bolts.
- Fighters launch from the rear bay every several seconds.
- Destroying the last battery collapses the shield. The hull then takes damage
  anywhere, the dreadnought moves faster, rings come more often, the exposed core
  fires aimed volleys, and condition goes RED.
- Ramming the hull bounces the ship away and hurts.
- If the ship is lost on the same tick the dreadnought falls, the run still ends:
  the kill scores, but the sector does not clear.
- A kill is worth 5,000 points per dreadnought level, a large salvage bonus, and
  a full hull repair before the next sector. Later dreadnoughts are tougher and
  fire faster. The upgrade screen before a dreadnought sector warns that one is
  waiting.

A status bar under the objective shows battery strength (the shield) and hull.
The radar marks the dreadnought with a pink diamond, and an edge arrow labeled
BOSS points to it when it is off screen.

## Lasers and view

The cannons fire red lasers at the start of every run. After two weapon upgrades
in the same run (any mix of Overclocked cannons, Split shot, and Heavy rounds)
they switch to blue lasers with a callout. Weapon cards that would make the
switch say so on the upgrade screen, and the weapons panel names the current
lasers and takes their color. Permanent hangar upgrades do not change the color.

The camera shows about 2,000 world units across a widescreen playfield, 25%
further than before. Fighters and dreadnoughts still arrive from beyond the edge
of the view: the browser reports how far the view reaches, so wide screens and
phones are covered too. The spawn ring also allows for the camera running ahead
of the ship at full boost with every engine upgrade.

## Controls

| Control | Action |
| --- | --- |
| Mouse movement | Point where you want to fly for smooth 360-degree steering |
| WASD or arrow keys | Turn smoothly toward a direction; release to keep the current heading |
| Space or left mouse button | Fire the fore and aft cannons. Hold to repeat; a fresh tap fires sooner |
| Shift | Hold to boost; energy recharges when released |
| E | Fire a pulse that clears nearby fighters, shots, and mines |
| P or Escape | Pause or resume |
| F or the Fullscreen button | Toggle fullscreen, including inside the dashboard. In fullscreen, Escape exits fullscreen and pauses the run |
| M | Toggle sound effects and arcade voice callouts, on by default |
| Enter | Launch from the title or game-over screen, including while a difficulty level has focus |
| Click a level, or arrow keys, Home, and End | Choose the difficulty on the title screen |
| Gamepad | Left stick or D-pad steers, A or RB fires, B or RT boosts, X or Y pulses, Start pauses. A or Start launches and resumes. On the title screen, left and right choose the difficulty; on the game-over screen, B returns to the title. On the upgrade screen the stick picks a card and A installs it |
| Touch | Virtual stick steers; hold FIRE and BOOST; tap PULSE |

Steering eases into the requested heading and keeps a little flight momentum.
Mouse, gamepad, and touch steering preserve every angle. The gamepad uses a
circular dead zone so shallow stick angles do not snap to a horizontal or
vertical direction. Keyboard or stick movement takes over from mouse steering;
move the mouse again to take control. Moving the pointer off the playfield or
over the ship holds the current heading. Pausing clears mouse steering.

There is no automatic fire. Older saved profiles that had auto-fire enabled are
read without that setting. A trigger still held when a menu appears does not
press the newly focused button. Releasing the trigger stops fire immediately;
a buffered press is consumed during the hold and cannot replay after release.

Leaving the Arcade panel, switching browser tabs, or moving focus to another
window pauses a run. Returning requires an explicit Resume. Hidden panels stop
the animation loop and suspend game audio. Profile switches unload the old game.
End Run and Bank Salvage opens the hangar without losing earned salvage. Damage
smoke stops once the ship is lost, so the game-over screen stops redrawing when
the last effects fade.

## Sound effects

The public package includes original, procedurally synthesized sounds for
cannons, explosions, engine, boost, and pulse. Fighters, cannon pods, rocks,
mines, fortress cores, and the player's ship have explosion cues.
Fortress and player-death blasts are pitched down;
small blasts have shorter tails. Each cannon volley starts one sound, including
when upgrades increase the firing rate. Releasing the trigger stops new shots
and lets the last shot's short tail finish naturally.

The ship has a quiet engine loop, a stronger boost loop while boosting, and a
separate pulse sound. Engine and boost use continuous loop boundaries
and short volume ramps. Flight loops stop at death and between sectors, as well
as on pause, mute, and leaving the game. Rapid boost presses cannot stack loops.

Sound is enabled for new profiles and begins on the Launch gesture. A saved
mute preference is respected. The five effects are local mono PCM WAV files in
`arcade/assets/sounds/`. There is no YouTube connection during play. The audio
context decodes them once when sound is enabled. Levels are normalized, simultaneous
samples are capped, and a compressor controls overlapping blasts. The
dreadnought adds synthesized cues for its warning, charge-up, plasma ring, shield
collapse, fighter launch, and destruction, plus a chime when the lasers turn blue;
voice callouts announce the dreadnought, its shield failing, its destruction, and
blue lasers. No callout plays once the ship is lost. Pausing,
muting, and leaving the game discard active sounds so they cannot replay on
resume. Existing synthesized system cues remain, and synthesized combat sounds
also cover missing or undecodable samples.

`arcade/assets/sounds/sources.json` records provenance, levels, and hashes.
`python tools/build_arcade_sounds.py` regenerates the original sounds offline
using NumPy and a fixed random seed. These assets use the repository's
Apache-2.0 license. No third-party sound recordings are distributed.

## Progress

Permanent hangar levels, banked salvage, highest score, furthest sector, run
count, highest cleared sector, chosen difficulty, and the sound preference are stored in browser localStorage. The key is
`data-void-patrol-v1:<dashboard-profile>`. Dashboard profiles have separate
progress. The standalone `/arcade/` page uses the separate `solo` key. Other
browsers and devices do not share these records.

Event Horizon uses pure-black surfaces with violet and ice-blue accents and
orbital artwork. It is available to every profile from the start, in Settings >
Appearance and the header theme toggle, alongside Minimal and Cyber; no arcade
progress is needed. The chosen theme is stored as `data-theme:<dashboard-profile>`,
separately for each profile. If storage is unavailable, a theme change lasts only
for the current visit. Inside the dashboard the arcade's top bar compacts sooner,
because dashboard themes use wider type.

Progress banks on sector completion, death, pause, page exit, and at five-second
intervals during a run. Every write starts from the saved record, so two open
tabs on one profile keep each other's salvage and purchases. A running flight itself is not saved across reloads.
If browser storage is blocked, the game remains playable and displays a notice
that progress is limited to the current visit. A corrupt record starts a fresh
hangar; numeric fields and upgrade levels are bounded before use.

## Implementation

- `arcade/engine.mjs`: deterministic, DOM-free simulation and upgrade economy.
  It steps in fixed 1/120-second ticks, so every frame rate flies the same path.
- `arcade/renderer.mjs`: Canvas 2D rendering with mipmapped sprites, a
  pure-black starfield, particles, fortress damage, and the radar.
- `arcade/game.mjs`: input, fullscreen, audio, progression, UI, and lifecycle.
- `arcade/index.html` and `arcade/style.css`: the full-viewport game, HUD, and
  screens.
- `arcade/host.js` and `arcade/host.css`: lazy dashboard embedding.
- `appearance.mjs` and `event_horizon.css`: profile-specific theme selection and
  the approved Event Horizon design. All three themes are available from the start.
- `arcade/assets/`: WebP sprites keyed from the supplied artwork (`ship`,
  `interceptor`, `manta`, `fortress`), the deep star layer (`stars`), and the
  ship cutaway shared with the Crew Deck (`cutaway`).
- `arcade_sprites.py`: regenerates the keyed sprites from source artwork.

The sky is pure black. The deepest layer is the supplied star image; finer
layers of individually placed stars with realistic color temperatures add
parallax depth without visible repetition. Fonts load from Google Fonts like
the Crew Deck; offline, the game falls back to system fonts and stays playable.

The dashboard's existing Sidebar View controls discover Arcade automatically.
The bridge explicitly serves the game HTML, JS modules, and styles from its
static-file allowlist; the WebP artwork uses the existing image asset route. No
new API or server-side game state is needed. A bridge process started before
the arcade routes were added needs its normal restart to serve them.

## Local preview and verification

From the repository root:

```powershell
python dashboard/arcade_preview.py --port 7817
```

Open the dashboard integration at http://localhost:7817/#arcade

Standalone play is at http://localhost:7817/arcade/

The preview uses a synthetic dashboard profile and disables live integrations,
AI, schedules, and payments.

```powershell
node --test dashboard/tests/test_arcade.mjs
python dashboard/tests/check_arcade_browser.py --base http://localhost:7817
python dashboard/tests/check_arcade_audio.py --base http://localhost:7817
python dashboard/tests/check_arcade_features.py --base http://localhost:7817
```

The browser check requires the existing Python Playwright environment and a
Chromium installation. It runs with isolated browser storage and blocks external
traffic. It verifies asset routes, constant flight, trigger-only fire on the
keyboard, mouse, and touch button, fullscreen on the F key and inside the
dashboard frame, upgrades, persistence, storage failures, profile isolation, and
390px/320px layouts. A simulated gamepad covers launch, pause, resume, and
upgrade choice without extra render loops. Further checks cover held keys across
screens, sub-frame taps, immediate trigger release, fullscreen exits, resizing
while paused, and two tabs sharing one profile. Screenshots and the result report are written under the
ignored `build/arcade-checks/` directory.

Verification checkpoint, 2026-10-30: all 20 simulation tests and the full browser
check passed, including eased turns, arbitrary headings, mouse steering and
keyboard takeover, precise gamepad and touch angles, controller drift rejection,
immediate trigger release, fullscreen inside the dashboard, mobile layouts,
upgrades, and isolated profile saves. No game
JavaScript errors were reported. Detailed results are in
`build/arcade-checks/browser-results.json`.

Shield fit, 2026-10-30: enlarged the player shield from 186 x 141 to 216 x 168
world units so its bright rim clears both swept-back wing tips. Checked the
sprite silhouette and browser renders at forward, diagonal, and sideways
headings with no JavaScript errors. Close-ups are in
`build/arcade-checks/shield-*.png`.

Sound checkpoint, 2026-10-30: all 20 simulation tests passed, and the focused
Chromium audio check passed all six groups with no JavaScript errors. It checks
the exact served files, decoded sample playback on trigger volleys, explosion
variants, bounded overlap, pause/resume, immediate mute, saved preferences, and
synthesized fallback for both missing and corrupt WAV files. The report is
`build/arcade-checks/audio-results.json`.

Gameplay checkpoint, 2026-11-01: difficulty levels, dreadnought sectors, red and
blue lasers, and the wider view. All 30 simulation tests passed, including
difficulty, escalation, dreadnought, laser, and view coverage. The full browser
check, the audio check, and the new feature check passed with no game JavaScript
errors. The feature check covers the difficulty selector (mouse, arrow keys,
persistence), both laser colors through the real upgrade flow, a complete
dreadnought fight from warning to full repair, and 390px and 320px layouts.
Screenshots are under `build/arcade-checks/features/`. A scripted pilot flying
attack runs beats the first Cadet dreadnought in about 100 seconds and gets the
first Pilot dreadnought to between 13% and 66% hull while ramming fighters and
the hull, so the fight is winnable but demands dodging.
`check_event_horizon.py` now confirms Event Horizon on a brand-new profile with no
arcade progress, per-profile persistence, the theme cycle, pure-black surfaces,
blocked storage, and phone layouts, including the phone vitals toggle.

Review fixes, 2026-11-01: a code review found that Enter did nothing while a
difficulty level had focus, and that dreadnought callouts could play over the
ship's destruction on the same tick. Both are fixed and covered by tests, along
with a spawn ring that clears the camera's look-ahead at full boost, damage smoke
that stops after death so the game-over screen stops redrawing, gamepad B from
game over to the title, one cached battery gradient instead of one per frame, and
pulse feedback that shows through dreadnought callouts. All 31 simulation tests
and every browser check passed with no game JavaScript errors.
