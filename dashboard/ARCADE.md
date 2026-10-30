# DATA Arcade

Void Patrol is a local, free-roaming space shooter in the spirit of the 1981
arcade game Bosconian, available from **Arcade** in the dashboard navigation.
It shares the Crew Deck's look: the same palette and type, FTL-style hull
segments and shield pips, system power bars, and chamfered panels. The player
ship, the alien fighters, and the six-cannon fortress are the supplied artwork.
There are no accounts, purchases, external game services, or AI calls to play.

## The loop

1. Launch a run. Your ship never stops flying; you steer it. Green squares on
   the radar and arrows at the screen edge lead to the stations.
2. Each station has six cannon pods that fire from their barrels at a ship in
   front of them. Destroy all six to expose the core, then destroy the core.
   The core hatch also cycles open every few seconds: one shot into the open
   core destroys the whole station at once for a 1,500-point direct hit.
3. Destroy every station to clear the sector, then install one of three system
   upgrades. The ship receives 25 hull repair, full shields, full boost, and more
   pulse charges before the next sector.
4. Collect salvage cogs and repair drops. Spend banked salvage on permanent hull,
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
| Enter | Launch from the title or game-over screen |
| Gamepad | Left stick or D-pad steers, A or RB fires, B or RT boosts, X or Y pulses, Start pauses. A or Start launches and resumes; on the upgrade screen the stick picks a card and A installs it |
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
End Run and Bank Salvage opens the hangar without losing earned salvage.

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
samples are capped, and a compressor controls overlapping blasts. Pausing,
muting, and leaving the game discard active sounds so they cannot replay on
resume. Existing synthesized system cues remain, and synthesized combat sounds
also cover missing or undecodable samples.

`arcade/assets/sounds/sources.json` records provenance, levels, and hashes.
`python tools/build_arcade_sounds.py` regenerates the original sounds offline
using NumPy and a fixed random seed. These assets use the repository's
Apache-2.0 license. No third-party sound recordings are distributed.

## Progress

Permanent hangar levels, banked salvage, highest score, furthest sector, run
count, and the sound preference are stored in browser localStorage. The key is
`data-void-patrol-v1:<dashboard-profile>`. Dashboard profiles have separate
progress. The standalone `/arcade/` page uses the separate `solo` key. Other
browsers and devices do not share these records.

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
