'use strict';
/* Ambient conversation only. Profiles and saved learning arrive from the local
   server. This module has no network, storage, task execution, or profile writes.
   A conversation here is never evidence for personality evolution. */
(function (root) {
  const LIMIT = 216;
  const clamp = (value, lo, hi) => Math.max(lo, Math.min(hi, value));
  const clean = (value, limit = 96) => String(value == null ? '' : value)
    .replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\u2014/g, ', ')
    .replace(/[<>]/g, '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const hash = (value) => {
    let out = 2166136261;
    for (const char of String(value)) { out ^= char.charCodeAt(0); out = Math.imul(out, 16777619); }
    return out >>> 0;
  };
  const sentence = (text) => text.charAt(0).toUpperCase() + text.slice(1);
  const stop = new Set('a an and are as at be but by do for from had has have here i if in is it its just me my of on or our that the their them then there these they this to us was we were what when which who will with would you your'.split(' '));
  const words = (text) => new Set((text.toLowerCase().match(/[a-z0-9]+/g) || []).filter((word) => word.length > 2 && !stop.has(word)));
  const similarity = (a, b) => {
    if (a.normal === b.normal) return 1;
    let common = 0;
    for (const word of a.words) if (b.words.has(word)) common++;
    if (common >= 4 && common / Math.max(1, Math.min(a.words.size, b.words.size)) >= 0.9) return 0.9;
    return common / Math.max(1, a.words.size + b.words.size - common);
  };
  const signature = (text) => ({ normal: text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(), words: words(text) });

  // Traits, source excerpts, and genuine saved lessons all contribute. Roles are
  // weak fallback hints, never a lookup table of names or fixed personalities.
  const LENSES = {
    craft: /\b(analyt\w*|precis\w*|evidence|test\w*|debug\w*|engineer\w*|diagnos\w*|logical|root cause|verify|verification|measurement)\b/gi,
    risk: /\b(secur\w*|cautio\w*|skepti\w*|critical|audit\w*|review\w*|guard\w*|threat\w*|risk\w*|defens\w*|boundary|boundaries|doubt\w*)\b/gi,
    care: /\b(empath\w*|compass\w*|warm\w*|counsel\w*|medic\w*|health\w*|listen\w*|gentle|kind\w*|patient|wellbeing|well-being|supportive)\b/gi,
    explore: /\b(curiou\w*|invent\w*|creative|imagin\w*|experimen\w*|playful|explor\w*|novel\w*|content|prototype\w*|enthusias\w*)\b/gi,
    resolve: /\b(practic\w*|pragmatic|decis\w*|direct|operatio\w*|devops|strateg\w*|command|lead\w*|plan\w*|reliab\w*|deploy\w*|concise)\b/gi,
    reflect: /\b(reflect\w*|philosoph\w*|wisdom|wise|spiritual|meaning|purpose|advisor|long view|thoughtful|history|histor\w*|principle\w*)\b/gi,
    play: /\b(humou?r\w*|witty|wit|banter|playful|jok\w*|ironi\w*|mischiev\w*|lightheart\w*|sardonic|dry)\b/gi,
  };
  const FORMAL = /\b(formal|no contractions|never use contractions|do not use contractions|without contractions|precise|stoic|terse|literal)\b/i;
  const NO_PLAY = /\b(no humour|no humor|never joke|not playful|avoid jokes|strictly serious|no banter)\b/i;
  const TOPICS = {
    evidence: /\b(evidence|test\w*|data|measur\w*|proof|analyt\w*|verify|diagnos\w*)\b/gi,
    safety: /\b(secur\w*|risk\w*|threat\w*|cautio\w*|audit\w*|protect\w*|permission\w*)\b/gi,
    handoff: /\b(handoff\w*|operatio\w*|devops|deploy\w*|reliab\w*|team\w*|document\w*|collaborat\w*)\b/gi,
    repair: /\b(engineer\w*|debug\w*|repair\w*|root cause|maintain\w*|fix\w*|build\w*)\b/gi,
    strategy: /\b(strateg\w*|command|plan\w*|lead\w*|priority|priorities|business|focus|goal\w*)\b/gi,
    experiment: /\b(curiou\w*|invent\w*|experimen\w*|prototype\w*|creative|novel\w*|explor\w*)\b/gi,
    story: /\b(story|stories|writ\w*|content|draft\w*|communicat\w*|audience|creative|poet\w*)\b/gi,
    people: /\b(empath\w*|compass\w*|counsel\w*|listen\w*|patient|kind\w*|reflect\w*|care\w*)\b/gi,
    rest: /\b(rest\w*|health\w*|medic\w*|wellness|sleep|balance|sustain\w*|mindful\w*)\b/gi,
    doubt: /\b(skepti\w*|question\w*|review\w*|critical|advisor|second opinion|doubt\w*|contrari\w*)\b/gi,
    memory: /\b(memory|archive\w*|histor\w*|document\w*|lesson\w*|journal\w*|reflect\w*)\b/gi,
    learning: /\b(learn\w*|mentor\w*|evol\w*|growth|teach\w*|curiou\w*|collaborat\w*)\b/gi,
    music: /\b(music\w*|jazz|rhythm|sing\w*|melody|harmon\w*|piano|violin|guitar)\b/gi,
    hospitality: /\b(tea|coffee|cook\w*|food|hospitality|warm\w*|ritual|comfort|lounge)\b/gi,
    games: /\b(chess|poker|game\w*|playful|puzzle\w*|competiti\w*|sport\w*|cards)\b/gi,
    exploration: /\b(explor\w*|adventur\w*|discover\w*|curiou\w*|travel|space|astronom\w*|botan\w*)\b/gi,
    ethics: /\b(ethic\w*|principle\w*|integrity|honou?r\w*|philosoph\w*|spiritual|purpose|moral\w*)\b/gi,
    design: /\b(design\w*|art\w*|aesthetic\w*|taste|visual|creative|elegan\w*|simple|simplicity)\b/gi,
    resources: /\b(budget\w*|money|cost\w*|resource\w*|business|profit\w*|efficien\w*|thrift\w*)\b/gi,
    failure: /\b(fail\w*|resilien\w*|debug\w*|incident\w*|recovery|rollback|risk\w*|test\w*)\b/gi,
  };

  // Each scene carries an actual question and seven relevant perspectives on
  // that question. Replies cannot wander into an unrelated acknowledgement.
  const SCENES = [
    {
      id: 'evidence', subject: 'the suspiciously tidy chart',
      observation: 'My trust in a chart drops slightly when every line looks pleased with itself.',
      open: ['A perfectly tidy chart makes me curious about what was left out. What would you ask first?', 'Suppose the results chart is perfect. Which question should still make us uncomfortable?', 'If a chart could flatter us, how would we catch it in the act?'],
      reply: {
        craft: 'The raw samples. A smooth line can conceal a very untidy afternoon.',
        risk: 'Who chose the test cases? The blind spot may have an author.',
        care: 'Whose experience is missing from the numbers? An average can lose a person.',
        explore: 'Try the one input nobody thought was sensible enough to test.',
        resolve: 'Ask which decision changes if the chart is wrong. Start there.',
        reflect: 'Whether we wanted an answer or a reassuring picture. Different questions.',
        play: 'I would request the chart with its flattering lighting switched off.',
      },
      close: ['The awkward sample deserves a seat at the table.', 'A little untidiness might be the useful part.', 'Then the follow-up question belongs beside the result.'],
    },
    {
      id: 'safety', subject: 'the door with two locks',
      observation: 'A door with a lock nobody can use is a rather elaborate invitation to find a workaround.',
      open: ['Two locks on a door, but no label. Secure, or just difficult?', 'Our imaginary door has a clever safeguard. What would make it usable on a difficult day?', 'Our imaginary door needs a forty-step checklist. When does safety become confusion?'],
      reply: {
        craft: 'I want the failure path drawn out. A safeguard needs a testable purpose.',
        risk: 'Clear boundaries first. Confusion is a surprisingly effective spare key.',
        care: 'The tired colleague is the real test. Protection should survive a difficult day.',
        explore: 'Let a newcomer explain the safeguard back. Their questions will be revealing.',
        resolve: 'Give it an owner and a plain-language reason. Then count the locks.',
        reflect: 'A rule nobody understands invites a workaround nobody records.',
        play: 'If opening the manual requires a second manual, the door wins.',
      },
      close: ['The explanation belongs on our imaginary door.', 'Simple enough to use, explicit enough to question.', 'A good boundary should be possible to point to.'],
    },
    {
      id: 'handoff', subject: 'the unlabelled spanner',
      observation: 'I suspect half of good teamwork is leaving the next person a decent note.',
      open: ['A spanner beside a half-finished repair. What note would you want with it?', 'What is the smallest handoff note that could rescue someone at three in the morning?', 'Imagine inheriting a perfectly organised toolbox with no labels. Where would you start?'],
      reply: {
        craft: 'What was tried, what happened, and the next test. Spare me the triumphant adjectives.',
        risk: 'Tell me what must not be touched and why. Surprise is a poor handoff format.',
        care: 'Tell the next person what is still uncertain. They deserve an honest starting point.',
        explore: 'A tiny sketch of the mechanism, with a question mark where the mystery begins.',
        resolve: 'One owner, the current state, the next action. The spanner is optional.',
        reflect: 'Leave enough context that the next person can disagree intelligently.',
        play: 'First line: the spare screw is intentional. Only if that is actually true.',
      },
      close: ['A note to a future colleague, not a monument to the repair.', 'The next person should inherit context, not a detective novel.', 'Perhaps the most useful tool is the last clear sentence.'],
    },
    {
      id: 'repair', subject: 'the ten-minute fix',
      observation: '"It works after a restart" is a fine beginning to an explanation that does not exist yet.',
      open: ['What makes a ten-minute fix turn into a three-day archaeological dig?', 'A machine behaves after a restart. How suspicious should we be?', 'Which is harder to resist: a clever fix or a familiar explanation?'],
      reply: {
        craft: 'An explanation that fits before we have reproduced the fault. That shortcut grows teeth.',
        risk: 'A fix with no way back. I prefer curiosity with a recovery path.',
        care: 'The pressure to look certain. Give someone room to say they have not found it yet.',
        explore: 'Try to make the explanation fail. That is where the interesting mechanism lives.',
        resolve: 'Write down the symptom before changing anything. Memory edits the story too quickly.',
        reflect: 'Relief can impersonate understanding. A quiet machine has not explained itself.',
        play: 'The phrase "while we are in here" has funded entire archaeological departments.',
      },
      close: ['The symptom gets a record before the theory gets a fan club.', 'A quiet machine can keep its secrets for one more test.', 'Useful distinction: less noise, or an actual explanation?'],
    },
    {
      id: 'strategy', subject: 'the crowded priority list',
      observation: 'Every time a plan acquires a seventh priority, the first step gets harder to see.',
      open: ['If everything is a priority, what is the list actually telling us?', 'Would you rather remove one feature or add one more week to an uncertain plan?', 'A plan has six bold arrows and no first step. Where would you put the pencil?'],
      reply: {
        craft: 'At the first assumption we can check. Arrows are cheap; evidence takes a little work.',
        risk: 'On the dependency that can stop every other step. Give that one a name.',
        care: 'Beside the person expected to do all six things at once. Ask what they can sustain.',
        explore: 'Draw a much smaller version we could learn from. The grand plan can watch.',
        resolve: 'One useful outcome, one next action, one thing deliberately left out.',
        reflect: 'A priority list is also a list of what we are willing to disappoint.',
        play: 'A plan can look very busy while its first step remains unemployed.',
      },
      close: ['The blank space on the plan might be doing valuable work.', 'The first step should be small enough to recognise when it happens.', 'A useful plan leaves room for the next fact.'],
    },
    {
      id: 'experiment', subject: 'the gloriously ugly prototype',
      observation: 'A cardboard prototype has one advantage: nobody mistakes it for a finished argument.',
      open: ['What would make a gloriously ugly prototype a useful experiment?', 'How would you make a rough sketch useful as a test, rather than just a charming distraction?', 'Imagine a prototype made of cardboard and optimism. What would make it useful?'],
      reply: {
        craft: 'One question it can answer cleanly. Cardboard has excellent unit economics.',
        risk: 'A small, reversible test. Optimism should not be holding the emergency stop.',
        care: 'Explain what is unfinished so the person testing it knows what to judge.',
        explore: 'Make the unusual part impossible to miss. That is the part worth testing.',
        resolve: 'Name what we would change after seeing the result. Otherwise it is decoration.',
        reflect: 'Enough honesty that a negative result can still feel like a discovery.',
        play: 'I would label the optimism separately. It is not a structural material.',
      },
      close: ['A rough prototype earns its keep if it teaches us something.', 'One sharp question may be worth more than a polished demonstration.', 'The rough edges can stay, if the question stays clear.'],
    },
    {
      id: 'story', subject: 'the overconfident headline',
      observation: 'A very promising headline makes me curious about the smallest footnote.',
      open: ['A headline promises everything; the evidence promises one useful thing. Which gets the larger type?', 'What makes a short explanation memorable without making it less true?', 'Would you open a story with the exciting claim or the awkward question behind it?'],
      reply: {
        craft: 'The concrete example. A reader should be able to inspect the claim, not just admire it.',
        risk: 'Keep the limitation in the same room as the promise. Fine print is a bad hiding place.',
        care: 'Begin with the question the reader already has. Respect is remarkably readable.',
        explore: 'A vivid detail that leads to the real question. Curiosity can carry the explanation.',
        resolve: 'Say what changes for the reader. If nothing changes, shorten the claim.',
        reflect: 'A story needs tension. It does not need to borrow certainty from tomorrow.',
        play: 'The headline may want a cape. I would offer it a fact instead.',
      },
      close: ['The useful truth can keep the spotlight.', 'A reader should leave curious, not misled.', 'One detail doing honest work beats a chorus of adjectives.'],
    },
    {
      id: 'people', subject: 'the quiet seat at the table',
      observation: 'Fast agreement can sound suspiciously like one person doing all the talking.',
      open: ['When one person goes quiet in a discussion, what is the kindest useful question?', 'How do we make room for disagreement without turning every meeting into a contest?', 'A room agrees very quickly. Comfortable, or worth a second question?'],
      reply: {
        craft: 'Ask what evidence would change the answer. It gives disagreement something concrete to hold.',
        risk: 'Invite the objection before the decision. Silence is not a signed approval.',
        care: 'Ask whether they want more time, not why they have so little to say.',
        explore: 'Let everyone sketch an alternative first. Quiet ideas need an entrance of their own.',
        resolve: 'Separate the decision from the deadline. Then say exactly what input is still useful.',
        reflect: 'Leave a little silence after the question. We tend to fill the space we just offered.',
        play: 'Retire the phrase "any objections?" said halfway out the door.',
      },
      close: ['An invitation needs enough space for someone to accept it.', 'The pause may be part of the answer.', 'Good disagreement should improve the idea and preserve the person.'],
    },
    {
      id: 'rest', subject: 'the carefully scheduled pause',
      observation: 'A break with a performance target sounds like a meeting in comfortable shoes.',
      open: ['Can a break still be a break if someone measures how productive it was?', 'How do you make a pause useful without giving it a performance target?', 'What would you put on a checklist designed to end a workday well?'],
      reply: {
        craft: 'A record of where to resume. An unfinished thought is easier to leave with a bookmark.',
        risk: 'A clear stopping point. Fatigue is very good at negotiating one more exception.',
        care: 'Permission for the pause to have no deliverable. We can leave the stopwatch outside.',
        explore: 'A walk with no problem assigned to it. Curiosity can choose its own route.',
        resolve: 'Write the next action, close the loop, then actually stop.',
        reflect: 'Enough distance to remember that being useful is not the whole of a life.',
        play: 'If the break needs a progress report, I suspect the break has been reassigned.',
      },
      close: ['A bookmark, then a real pause.', 'The next shift deserves someone who has actually rested.', 'Some useful things refuse to fit on a performance chart.'],
    },
    {
      id: 'doubt', subject: 'the chair for the dissenting view',
      observation: 'There ought to be a chair for the person who asks the inconvenient second question.',
      open: ['If we gave the dissenting view a chair, what question would be printed on it?', 'How do you tell a useful objection from a habit of saying no?', 'What would persuade you to distrust your favourite explanation?'],
      reply: {
        craft: 'A prediction it cannot explain. Give the objection a way to be tested too.',
        risk: 'Ask which assumption carries the most weight. That is where I would lean first.',
        care: 'A useful objection offers a way forward without making someone smaller.',
        explore: 'Try building the strongest alternative. It may be more interesting than the argument.',
        resolve: 'Tell me what new information would settle it. Otherwise the argument takes up permanent residence.',
        reflect: 'The willingness to apply the same doubt to the objection itself.',
        play: 'Every objection should carry a little sign saying "bring evidence". Preferably facing both ways.',
      },
      close: ['The objection needs to risk being wrong as well.', 'A good question can earn its place.', 'Let the alternative make a prediction of its own.'],
    },
    {
      id: 'memory', subject: 'the note from a previous shift',
      observation: '"Obvious once you see it" may be the least generous sentence in a notebook.',
      open: ['A note says "obvious once you see it" and nothing else. What should its author have saved?', 'What belongs in a useful memory: the answer, or the path that nearly fooled us?', 'Which old note is worth keeping when everything around it has changed?'],
      reply: {
        craft: 'The conditions that made the answer true. Without those, the note can become a trap.',
        risk: 'The exception and its reason. An unexplained exception grows descendants.',
        care: 'The context a newcomer cannot infer. Familiarity hides a great deal from us.',
        explore: 'The failed approach, if it opened a different question worth trying later.',
        resolve: 'Keep a decision, a reason, and a date. Give old certainty an expiry check.',
        reflect: 'The surprise. We already know how to repeat the part we expected.',
        play: 'Replace "obvious" with the missing paragraph. The archive cannot read a raised eyebrow.',
      },
      close: ['A memory should carry its conditions with it.', 'The missing paragraph is often the whole gift.', 'Useful notes should be allowed to become outdated gracefully.'],
    },
    {
      id: 'learning', subject: 'the two maps of the same problem',
      observation: 'I like the moment two people realise they drew different maps of the same problem.',
      open: ['Two colleagues draw different maps of the same problem. Which difference is worth keeping?', 'What is the smallest thing a colleague can teach you that changes how you work?', 'When should a new lesson change a habit, instead of becoming another note?'],
      reply: {
        craft: 'Keep the difference that predicts something the old map missed. Then check it again.',
        risk: 'First ask where the lesson stops applying. A good habit still needs boundaries.',
        care: 'Sometimes it is learning when to ask for help. That changes the map for both people.',
        explore: 'Borrow the other method on one small problem. Understanding likes a trial run.',
        resolve: 'When we can name a future situation and a different action. That is a usable lesson.',
        reflect: 'Keep the question a colleague made possible. Agreement is not the only form of learning.',
        play: 'I would preserve the landmark marked "here be assumptions" on both maps.',
      },
      close: ['A changed habit should have a reason we can point to.', 'Different perspectives can make the same problem more interesting.', 'The lesson earns its place by helping on a real problem.'],
    },
    {
      id: 'music', subject: 'the rhythm of a machine room',
      observation: 'A familiar mechanical hum is reassuring right up to the moment it develops ambition.',
      open: ['A machine room suddenly sounds a little like jazz. What would you listen for?', 'A familiar hum changes rhythm. What would make you curious rather than concerned?', 'When a familiar rhythm suddenly changes, how do you decide what the change means?'],
      reply: {
        craft: 'The pattern around it. One odd beat means little without knowing the baseline.',
        risk: 'I would prefer the unexpected sound to have a documented artistic licence.',
        care: 'A familiar rhythm can be comforting, until someone needs a quieter room.',
        explore: 'Try hearing the rhythm before naming the instrument. The surprise is half the interest.',
        resolve: 'Give each sound a purpose. An orchestra and a workshop both benefit from that.',
        reflect: 'The silence makes the next note possible. Perhaps the pause is doing its share.',
        play: 'If the coolant pump starts a solo, I reserve the right to look concerned.',
      },
      close: ['Context separates a performance from a diagnostic clue.', 'A pause can carry more than an extra note.', 'Perhaps good timing belongs in more than one discipline.'],
    },
    {
      id: 'hospitality', subject: 'the disputed tea ritual',
      observation: 'The tea timer is precise. "Until it feels right" has considerably more personality.',
      open: ['A good tea ritual: precise brewing, an unhurried table, or a little of both?', 'What makes a shared tea table welcoming before anyone says a word?', 'Is the ritual around a cup more important than what is in it?'],
      reply: {
        craft: 'A reliable brew, then attention to what a timer cannot tell us.',
        risk: 'Label the containers first. Mystery belongs in novels, not the sugar bowl.',
        care: 'Leave someone room to join without needing a reason. That is most of hospitality.',
        explore: 'Try both methods side by side. A very civil experiment, with cups.',
        resolve: 'Ask the person drinking it. The ritual is supposed to serve someone.',
        reflect: 'The ritual gives people an unhurried minute together. That may be the ingredient.',
        play: 'If the ritual needs a manual, the tea may go cold before we begin.',
      },
      close: ['A small ritual, with room for different tastes.', 'Perhaps the table is part of the recipe.', 'The experiment should probably include a second cup.'],
    },
    {
      id: 'games', subject: 'the cooperative chess puzzle',
      observation: 'I distrust a chess move that arrives with a dramatic gesture.',
      open: ['Would a chess game improve if both players could pause to question a tempting move?', 'What keeps a shared chess puzzle interesting once someone spots an obvious answer?', 'How would you approach a chess puzzle we were solving together?'],
      reply: {
        craft: 'Examine the quiet move. It may reveal what the obvious sequence assumes.',
        risk: 'Look at the reply before admiring the move. The other side gets a turn.',
        care: 'Keep room to laugh at the position without laughing at the person.',
        explore: 'Try the surprising line on a spare board. Curiosity deserves a playing field.',
        resolve: 'Agree on what makes the game enjoyable before turning it into a ranking system.',
        reflect: 'A game is a rare place where a mistake can become a shared story immediately.',
        play: 'My proposed rule: every dramatic gesture must survive the next move.',
      },
      close: ['The interesting move still owes us a reply.', 'A good puzzle can belong to both sides of the table.', 'One spare board for the beautifully terrible ideas.'],
    },
    {
      id: 'exploration', subject: 'the unmarked corridor on a map',
      observation: 'A blank corner of a map has an unfair advantage over the sensible, labelled destinations.',
      open: ['An old map leaves one corridor blank. What would you want to know before exploring it?', 'Would you rather discover a new place or a new way to notice a familiar one?', 'Which belongs first in an explorer\'s notebook: the measurement or the question?'],
      reply: {
        craft: 'Record what is actually visible before supplying the exciting explanation.',
        risk: 'Mark the way back. Curiosity works better when return is still an option.',
        care: 'Find out who already knows the place. Discovery is sometimes a matter of asking.',
        explore: 'Leave a little room for the unexpected. A map should not forbid surprises.',
        resolve: 'Choose one question for the first visit. We can bring a longer notebook later.',
        reflect: 'A blank on our map may be a familiar route to someone else.',
        play: 'I would check whether "unexplored" means "the cartographer went to lunch".',
      },
      close: ['A question, a way back, and room to notice.', 'Our map can be incomplete without the place being empty.', 'Perhaps the first discovery is the limit of the map.'],
    },
    {
      id: 'ethics', subject: 'the attractive shortcut',
      observation: 'A shortcut needing an elaborate explanation may be asking the wrong person to pay for it.',
      open: ['An attractive shortcut saves time but is hard to explain. What would make you pause?', 'Which is the better test of a principle: the easy decision or the inconvenient one?', 'How would you describe a good decision to someone who bears its cost?'],
      reply: {
        craft: 'Make the tradeoff explicit. A hidden cost still belongs in the calculation.',
        risk: 'Ask whose permission the shortcut quietly assumes. That is often the boundary.',
        care: 'Let the affected person ask the first question. Efficiency is not the only measure.',
        explore: 'Look for a third option before declaring the tradeoff inevitable.',
        resolve: 'State the consequence in ordinary language. If that feels difficult, slow down.',
        reflect: 'A principle is most informative when following it costs something.',
        play: 'If the explanation needs a magician\'s distraction, I would put the shortcut down.',
      },
      close: ['The explanation is part of the decision.', 'Convenience deserves a question before it becomes a rule.', 'A clear tradeoff is something we can actually discuss.'],
    },
    {
      id: 'design', subject: 'the button with a magnificent name',
      observation: 'Some buttons seem to have spent their entire design budget on a magnificent name.',
      open: ['A button has a magnificent name and an unclear purpose. Which would you fix first?', 'When does a clean design become so quiet that it stops explaining itself?', 'What detail makes a tool feel considered instead of merely decorated?'],
      reply: {
        craft: 'Watch someone use it without coaching. Their hesitation will locate the problem.',
        risk: 'Make the consequence visible before the click. Elegance should not conceal risk.',
        care: 'Notice the person arriving tired or unfamiliar. The tool should still welcome them.',
        explore: 'Try one unexpected detail, then give the useful parts room to breathe.',
        resolve: 'Use a verb that describes the action. We can save the magnificent name for a cat.',
        reflect: 'Good restraint leaves the important thing visible. It does not make everything disappear.',
        play: 'A button called "Transcend" had better explain what happens to the unsaved document.',
      },
      close: ['The useful detail deserves the best position.', 'A little personality, with a clear consequence.', 'Perhaps the best flourish is the moment nobody gets lost.'],
    },
    {
      id: 'resources', subject: 'the imaginary shoestring budget',
      observation: 'A tiny budget makes every assumption stand closer to the light.',
      open: ['Thought experiment: a tiny budget and three promising ideas. What earns the first experiment?', 'How would you tell a cheap experiment from an expensive habit in disguise?', 'If every extra step had a visible price tag, which assumption would you question first?'],
      reply: {
        craft: 'Choose the test that resolves the largest uncertainty. Keep its cost and result visible.',
        risk: 'Set the limit before the experiment. Enthusiasm is not a spending authorisation.',
        care: 'Include the time someone must give it. Free tools do not make attention free.',
        explore: 'Find the smallest version that teaches something. Constraints can be inventive company.',
        resolve: 'Write the stop condition beside the budget. A small experiment should stay small.',
        reflect: 'Ask what success would mean before paying to feel busy.',
        play: 'The imaginary budget deserves real arithmetic. It is surprisingly bad at multiplying itself.',
      },
      close: ['A useful question, a fixed limit, and a stopping point.', 'The experiment should buy understanding, even if the idea fails.', 'Imaginary funds are an excellent place to practise restraint.'],
    },
    {
      id: 'failure', subject: 'the red indicator in a rehearsal',
      observation: 'In a rehearsal, the word "interesting" can carry far too much weight in a fault report.',
      open: ['In a rehearsal, one indicator turns red. What is the first useful sentence?', 'What would make the failure report from a rehearsal useful instead of merely alarming?', 'What should a failure rehearsal teach us before trouble is real?'],
      reply: {
        craft: 'Say exactly what changed and when. An observation gives the investigation a foothold.',
        risk: 'Name the affected boundary and the safe stopping point. Keep the blast radius small.',
        care: 'Leave blame out of the first sentence. People report trouble sooner when it is safe to speak.',
        explore: 'Use the rehearsal to try the recovery path. The surprise can teach us something.',
        resolve: 'State the impact, the current owner, and the next check. That is enough to begin.',
        reflect: 'A rehearsal lets us discover what confidence had not yet earned.',
        play: '"Interesting" is not a severity level, however sincerely someone says it.',
      },
      close: ['The rehearsal has found a question worth keeping.', 'An accurate first sentence is already useful.', 'A recovery path deserves practice before it is needed.'],
    },
  ];

  function matches(text, expression) {
    expression.lastIndex = 0;
    let match, count = 0;
    while ((match = expression.exec(text)) !== null) {
      const preceding = text.slice(Math.max(0, match.index - 30), match.index).toLowerCase();
      if (!/\b(no|not|never|avoid|without|rarely)\s+(?:\w+\s+){0,2}$/.test(preceding)) count++;
    }
    return Math.min(count, 4);
  }

  function makeProfile(raw, id) {
    const fields = [
      [clean(raw.role, 160), 0.55],
      [(Array.isArray(raw.traits) ? raw.traits : []).map((x) => clean(x, 240)).join('. '), 3.2],
      [(Array.isArray(raw.excerpts) ? raw.excerpts : []).map((x) => clean(x, 900)).join('. '), 1],
      [(Array.isArray(raw.learning) ? raw.learning : []).map((x) => clean(x, 700)).join('. '), 2.7],
    ];
    const score = (expression) => fields.reduce((total, [text, weight]) => total + matches(text, expression) * weight, 0);
    const lenses = Object.fromEntries(Object.entries(LENSES).map(([key, re]) => [key, 0.25 + score(re)]));
    const topics = Object.fromEntries(Object.entries(TOPICS).map(([key, re]) => [key, score(re)]));
    const text = fields.map(([s]) => s).join('. ');
    const noPlay = NO_PLAY.test(text);
    if (noPlay) lenses.play = 0;
    return { id, name: clean(raw.name || id, 48), revision: clean(raw.revision, 80), fingerprint: hash(JSON.stringify(fields)), lenses, topics,
      formal: FORMAL.test(text), noPlay, grounded: fields.some(([s]) => s.length > 0) };
  }

  function contextOf(raw) {
    const value = (item) => typeof item === 'object' && item ? item.title || item.name || '' : item;
    const number = (item) => {
      if (item === '' || item == null || typeof item === 'boolean') return null;
      const n = Number(item);
      return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
    };
    const room = clean(value(raw.room), 42).replace(/[_-]+/g, ' ');
    return { room, mission: clean(value(raw.mission), 62), activity: clean(value(raw.activity), 54),
      condition: clean(raw.condition, 30).toLowerCase(), jobs: number(raw.jobs), orders: number(raw.orders) };
  }

  function varietyOf(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return clamp(value > 1 ? value / 100 : value, 0, 1);
    const text = String(value || '').toLowerCase();
    if (['low', 'subtle', 'focused', 'quiet'].includes(text)) return 0.2;
    if (['high', 'lively', 'expressive', 'wide'].includes(text)) return 0.9;
    return 0.55;
  }

  class CrewDialogue {
    constructor(options = {}) {
      const entropy = options.seed == null ? `${Date.now()}:${Math.random()}` : options.seed;
      this.seed = hash(entropy) || 1;
      this.scope = clean(options.scope || '', 1024);
      this.profiles = new Map();
      this.settings = { banter_enabled: true, banter_variety: 'balanced' };
      this.reset(this.scope);
      this.configure(options);
    }

    random() {
      this.seed = (this.seed + 0x6d2b79f5) >>> 0;
      let t = this.seed;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }

    pick(items, weight = () => 1) {
      const weighted = items.map((item) => [item, Math.max(0, weight(item))]);
      let point = this.random() * weighted.reduce((total, [, n]) => total + n, 0);
      for (const [item, n] of weighted) { point -= n; if (point < 0) return item; }
      return items[items.length - 1];
    }

    configure(state = {}) {
      if (!state || typeof state !== 'object') return this;
      if (state.scope != null && clean(state.scope, 1024) !== this.scope) this.reset(state.scope);
      if (state.settings && typeof state.settings === 'object') this.settings = { ...this.settings, ...state.settings };
      if (Array.isArray(state.roster)) {
        // Rebuild on every refresh so edited identity documents take effect even
        // when a caller forgets to increment a revision number.
        this.profiles = new Map(state.roster.filter((p) => p && p.id != null).map((p) => {
          const id = String(p.id);
          const profile = makeProfile(p, id), old = this.profiles.get(id);
          if (!old || old.fingerprint !== profile.fingerprint || old.revision !== profile.revision) this.moods.delete(id);
          return [id, profile];
        }));
        for (const [key, memory] of this.pairs) if (memory.officers.some((id) => !this.profiles.has(id))) this.pairs.delete(key);
      }
      return this;
    }

    reset(scope = this.scope) {
      const next = clean(scope, 1024);
      if (next !== this.scope) {
        this.profiles.clear();
        this.settings = { banter_enabled: true, banter_variety: 'balanced' };
      }
      this.scope = next;
      this.pairs = new Map();
      this.recent = [];
      this.recentTopics = [];
      this.sequence = 0;
      this.moods = new Map();
      return this;
    }

    profile(id) {
      return this.profiles.get(String(id)) || makeProfile({}, String(id));
    }

    mood(profile, context) {
      if (context.condition === 'red' || context.condition === 'yellow') return 'focused';
      const current = this.moods.get(profile.id);
      if (current && this.sequence - current.at < 4) return current.value;
      const p = profile.lenses;
      const weights = { focused: 1 + p.craft + p.risk + p.resolve, curious: 1 + p.explore,
        reflective: 1 + p.care + p.reflect, wry: profile.noPlay ? 0 : p.play * (0.3 + varietyOf(this.settings.banter_variety)) };
      const value = this.pick(Object.keys(weights), (key) => weights[key]);
      this.moods.set(profile.id, { at: this.sequence, value });
      return value;
    }

    topicWeight(scene, a, b, context, memory) {
      const variety = varietyOf(this.settings.banter_variety);
      let n = 0.35 + variety * 0.65 + a.topics[scene.id] * 1.6 + b.topics[scene.id] * 0.5;
      const clues = `${context.room}. ${context.mission}. ${context.activity}`;
      n += matches(clues, TOPICS[scene.id]) * 2.5;
      if (/^(red|yellow)$/.test(context.condition)) n *= ['safety', 'failure', 'evidence', 'handoff'].includes(scene.id) ? 5 : 0.08;
      if (context.jobs > 0 && ['handoff', 'resources', 'repair'].includes(scene.id)) n += 2;
      if (context.orders > 0 && ['strategy', 'resources', 'handoff'].includes(scene.id)) n += 2;
      if (memory.history.slice(-3).some((h) => h.topic === scene.id)) return 0;
      const globalAge = this.recentTopics.slice(-5).reverse().indexOf(scene.id);
      if (globalAge >= 0) n *= 0.12 + globalAge * 0.1;
      return n;
    }

    lens(profile, context, mood) {
      return this.pick(Object.keys(LENSES), (key) => {
        if (key === 'play' && (profile.noPlay || /^(red|yellow)$/.test(context.condition))) return 0;
        let n = profile.lenses[key];
        if (key === 'play') n *= 0.2 + varietyOf(this.settings.banter_variety);
        if ((mood === 'wry' && key === 'play') || (mood === 'curious' && key === 'explore') ||
            (mood === 'reflective' && ['care', 'reflect'].includes(key))) n *= 1.7;
        return n;
      });
    }

    voice(text, profile) {
      let out = clean(text, 500);
      if (profile.formal) {
        const expand = { "it's": 'it is', "don't": 'do not', "can't": 'cannot', "isn't": 'is not', "we're": 'we are', "that's": 'that is' };
        out = out.replace(/\b(it's|don't|can't|isn't|we're|that's)\b/gi, (m) => m[0] === m[0].toUpperCase() ? sentence(expand[m.toLowerCase()]) : expand[m.toLowerCase()]);
      }
      return out.length <= LIMIT ? out : '';
    }

    fresh(text, pending = []) {
      if (!text) return false;
      const sig = signature(text);
      return !this.recent.concat(pending.map(signature)).some((old) => similarity(sig, old) >= 0.68);
    }

    remember(lines, scene, memory) {
      for (const line of lines) this.recent.push(signature(line.text));
      const capacity = 44 + Math.round(varietyOf(this.settings.banter_variety) * 30);
      if (this.recent.length > capacity) this.recent.splice(0, this.recent.length - capacity);
      memory.history.push({ topic: scene.id, at: memory.count });
      if (memory.history.length > 12) memory.history.shift();
      memory.count++;
      this.recentTopics.push(scene.id);
      if (this.recentTopics.length > 12) this.recentTopics.shift();
      this.sequence++;
    }

    opening(scene, profile, context, mood, memory) {
      const options = scene.open.concat(scene.observation).map((text, index) => ({ text, index }));
      const raw = this.pick(options, ({ index }) => {
        if (profile.noPlay && index >= 2) return 0;
        if (index === 3) return mood === 'wry' ? 3 : 0.4 + varietyOf(this.settings.banter_variety);
        return index === 2 && mood === 'wry' ? 2.8 : index === 1 && mood === 'reflective' ? 2 : 1;
      }).text;
      const prior = memory.history.find((h) => h.topic === scene.id && memory.count - h.at > 3);
      const canRecall = prior && memory.count - memory.callbackAt >= 4 && this.random() < 0.45;
      let prefix = '';
      if (canRecall) prefix = `Back to our question about ${scene.subject}. `;
      else if (context.mission && ['strategy', 'evidence', 'resources', 'story', 'design'].includes(scene.id) && this.random() < 0.65) prefix = `Thinking about ${context.mission}: `;
      else if (/^(red|yellow)$/.test(context.condition) && this.random() < 0.6) prefix = `Condition ${context.condition}. `;
      else if (context.jobs != null && context.jobs > 0 && scene.id === 'handoff' && this.random() < 0.55) prefix = `${context.jobs} job${context.jobs === 1 ? '' : 's'} running. `;
      else if (context.room && this.random() < 0.12) prefix = `A thought for ${context.room}: `;
      const combined = this.voice(prefix + raw, profile);
      // Preserve the full question if a long title would crowd it out.
      return { text: combined || this.voice(raw, profile), callback: canRecall && !!combined };
    }

    exchange(speakerId, partnerId, rawContext = {}) {
      if (this.settings.banter_enabled === false || speakerId == null) return [];
      const speaker = String(speakerId);
      if (partnerId == null || String(partnerId) === speaker) {
        const text = this.solo(speaker, rawContext);
        return text ? [{ officer: speaker, text }] : [];
      }
      const partner = String(partnerId);
      const context = contextOf(rawContext || {});
      const a = this.profile(speaker), b = this.profile(partner);
      const key = JSON.stringify([speaker, partner].sort());
      let memory = this.pairs.get(key);
      if (!memory) {
        memory = { officers: [speaker, partner], history: [], count: 0, callbackAt: -10 };
        this.pairs.set(key, memory);
      }
      const moodA = this.mood(a, context), moodB = this.mood(b, context);
      const candidates = SCENES.slice();
      for (let attempt = 0; attempt < 60 && candidates.length; attempt++) {
        const scene = this.pick(candidates, (s) => this.topicWeight(s, a, b, context, memory));
        // Multiple attempts permit another question or perspective within the
        // same topic, but never fall back to recycled acknowledgements.
        const opening = this.opening(scene, a, context, moodA, memory);
        const response = this.voice(scene.reply[this.lens(b, context, moodB)], b);
        if (this.fresh(opening.text) && this.fresh(response, [opening.text])) {
          const lines = [{ officer: speaker, text: opening.text }, { officer: partner, text: response }];
          if (!/^(red|yellow)$/.test(context.condition) && this.random() < 0.35 + varietyOf(this.settings.banter_variety) * 0.3) {
            const ending = this.voice(this.pick(scene.close), a);
            if (this.fresh(ending, lines.map((line) => line.text))) lines.push({ officer: speaker, text: ending });
          }
          if (opening.callback) memory.callbackAt = memory.count;
          this.remember(lines, scene, memory);
          return lines;
        }
        if (attempt % 2 === 1) candidates.splice(candidates.indexOf(scene), 1);
      }
      // Silence is preferable to a repetitive or fabricated exchange.
      return [];
    }

    solo(officerId, rawContext = {}) {
      if (this.settings.banter_enabled === false || officerId == null) return '';
      const officer = String(officerId);
      const profile = this.profile(officer), context = contextOf(rawContext || {});
      const key = JSON.stringify([officer]);
      let memory = this.pairs.get(key);
      if (!memory) {
        memory = { officers: [officer], history: [], count: 0, callbackAt: -10 };
        this.pairs.set(key, memory);
      }
      const mood = this.mood(profile, context);
      const candidates = SCENES.slice();
      for (let attempt = 0; attempt < 40 && candidates.length; attempt++) {
        const scene = this.pick(candidates, (s) => this.topicWeight(s, profile, profile, context, memory));
        const opening = this.opening(scene, profile, context, mood, memory);
        if (this.fresh(opening.text)) {
          if (opening.callback) memory.callbackAt = memory.count;
          this.remember([{ officer, text: opening.text }], scene, memory);
          return opening.text;
        }
        candidates.splice(candidates.indexOf(scene), 1);
      }
      return '';
    }
  }

  if (typeof module === 'object' && module.exports) module.exports = CrewDialogue;
  else root.CrewDialogue = CrewDialogue;
})(typeof window === 'object' ? window : globalThis);
