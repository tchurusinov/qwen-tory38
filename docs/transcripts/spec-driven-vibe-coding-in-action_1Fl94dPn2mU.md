---
id: yt-1Fl94dPn2mU
name: Spec-driven vibe coding in action
description: By viewer request, let's look at the "spec-driven vibe coding" development process mentioned in an earlier episode. We'll combine the fun of vibe-coding (getting the LLM to write 100% of the code for us) with the rigor and development discipline of only writing code from a detailed specification. We
license: CC-BY-4.0
sources:
- https://www.youtube.com/watch?v=1Fl94dPn2mU
tags:
- youtube
- channel-no-place-like-localhost
- captions-auto-generated
classifications:
  category: video-transcript
  platform: youtube
  source_type: auto-generated
video_id: 1Fl94dPn2mU
channel_name: No place like localhost
view_count: 8763
upload_date: '20260918'
caption_track_type: auto-generated
segment_count: 1101

---

**00:00:00**A couple of episodes ago, I talked about

**00:00:01**a development methodology that I've been

**00:00:03**using in my own projects, and I

**00:00:04**tongue-in-cheek called it specdriven

**00:00:06**vibe coding. I still like that term. The

**00:00:08**idea is we combine the best of both

**00:00:10**worlds. We combine the fun of vibe

**00:00:12**coding, getting the local LLM to

**00:00:14**generate literally 100% of the code for

**00:00:16**us with the rigor and discipline of

**00:00:18**software engineering best practices,

**00:00:19**which involve uh writing code only from

**00:00:22**a very very detailed specification. Uh

**00:00:25**there were a few comments in that video

**00:00:27**saying that people would like to see

**00:00:28**that process in action. That process of

**00:00:30**bouncing a spec doc back and forth

**00:00:32**between myself and the LLM a bunch of

**00:00:33**times until it's really really not

**00:00:35**locked down and only then proceeding to

**00:00:37**write the code. Uh people wanted to see

**00:00:39**an example of that uh process in action.

**00:00:42**And I thought really really locking down

**00:00:46**a specification document doesn't exactly

**00:00:48**make for riveting footage. But okay.

**00:00:50**Okay. I happen to be starting up a new

**00:00:52**project. So, it's a great time to jump

**00:00:53**in on the ground floor and start

**00:00:55**developing this project entirely from

**00:00:57**specifications. So, let's take a look at

**00:00:58**spec driven vibe coding in action and

**00:01:00**see what it can do for us. [music]

**00:01:09**>> Okay, brand new project. Now, I should

**00:01:11**stress at the beginning that if we want

**00:01:13**to adopt this methodology, it doesn't

**00:01:15**have to be on a brand new project. It

**00:01:17**helps. It helps if we're starting from a

**00:01:18**completely blank slate because we can go

**00:01:20**in from day one with a completely spec

**00:01:23**driven mindset. We can say there's no

**00:01:24**code that's going to be written for this

**00:01:25**project that isn't backed by a detailed

**00:01:27**specification and in theory we keep the

**00:01:30**architecture nice and clean the whole

**00:01:32**way through that project in theory. Uh

**00:01:35**but what if we have a project that's

**00:01:36**been in flight for a while and it's

**00:01:37**built up an existing codebase? Uh what

**00:01:40**you can do is just point the LLM at it

**00:01:42**at the codebase and whatever

**00:01:43**documentation you have for it and say I

**00:01:45**want you to reverse engineer a set of

**00:01:47**detail specification documents uh for

**00:01:48**this project as written. LLM are really

**00:01:51**good at that sort of thing. And then

**00:01:52**from that day forward you can adopt the

**00:01:54**specdriven approach. Not a big deal. For

**00:01:57**this example uh we're going to be

**00:01:59**starting from a completely blank

**00:02:00**project. I mean where do we even begin?

**00:02:02**Let's let's start on GitHub. I've

**00:02:04**created a project and I've created uh

**00:02:05**just a docs folder. That's basically all

**00:02:07**that exists right now. Just this docs

**00:02:08**folder. Let's look at the readme.

**00:02:10**Architecture talks for the project. Go

**00:02:12**here. This is an entirely spec driven

**00:02:14**project. For every new feature, the

**00:02:15**human develops the spec. The LLM reviews

**00:02:17**the spec and helps refine it. And then

**00:02:19**the LLM writes the code. So there's

**00:02:20**there's those are the three steps that

**00:02:22**we're going to see over and over and

**00:02:23**over again. The human, that's me, uh I

**00:02:25**sit down and write the specification.

**00:02:26**And then step two is the back and forth

**00:02:28**dance. And this can take a long time. Uh

**00:02:31**there might be multiple passes for each

**00:02:33**dock. And that's fine. Uh, it's

**00:02:35**important if you're going to spend time

**00:02:36**on any one step, it's going to be this

**00:02:38**middle step where we're bouncing the

**00:02:39**document back and forth. I I give it to

**00:02:41**the LLM as we'll see and say, "I want

**00:02:42**you to read it. Don't write the code

**00:02:44**yet. Just read it and critique it." And

**00:02:45**I adjust the spec and uh then we do it

**00:02:48**again. And then we do it again and we

**00:02:49**keep doing it until the LLM has no more

**00:02:51**major complaints or until I'm satisfied

**00:02:52**that the spec uh is in a good enough

**00:02:54**state to be implemented. And then step

**00:02:56**three, the LLM, my coding buddy Quen

**00:02:59**3.827B, writes the code. This project

**00:03:01**will be 100% AI generated. uh at least

**00:03:04**the codebase. I'm not going to uh write

**00:03:06**a single line of code for this project.

**00:03:08**That's my challenge to myself. Uh so I

**00:03:12**write the spec, Quinn and I review the

**00:03:14**spec, and then Quinn writes the code. Uh

**00:03:16**if we're changing existing code, uh the

**00:03:18**human, that's me, updates the spec andor

**00:03:20**adds a new spec that supersedes an older

**00:03:22**one. We'll see the ordering of the spec

**00:03:24**docs as we go through this. Uh and then

**00:03:26**we do the same back and forth. Then step

**00:03:27**two is always the same. we we bounce the

**00:03:29**the the new document or the updated

**00:03:30**document back and forth, back and forth

**00:03:32**until we're both happy that it's uh

**00:03:34**specified to a a good enough level of

**00:03:36**detail and then the LLM adjusts the

**00:03:38**code. And for bug fixes, we we we call

**00:03:41**them as we see them. Some bug fixes are

**00:03:43**going to be so small like the the label

**00:03:44**on this button is wrong or the the color

**00:03:46**of this thing is is incorrect. Who

**00:03:48**cares? We're not going to update a spec

**00:03:49**doc for that. Uh we can just do the do

**00:03:52**the change. But any bug fix that

**00:03:53**requires a change to the way the code

**00:03:55**works, we're going to have to look

**00:03:56**carefully at the spec docs and see if

**00:03:58**any of them need to be updated. It's

**00:03:59**important to keep the spec up to date

**00:04:02**with the code. Uh we don't want them to

**00:04:04**drift apart from one another because it

**00:04:06**leads to confusion down the road. If we

**00:04:07**point the LLM uh at a new feature

**00:04:10**request or a bug fix or whatever and it

**00:04:11**it sees there's a discrepancy between

**00:04:13**what the spec says and what the code

**00:04:14**does, now we got to stop and untangle

**00:04:16**that. So whenever we make a code change,

**00:04:18**we check the spec docs to make sure that

**00:04:20**they're both in sync and vice versa. So

**00:04:22**we update the spec, we update the code,

**00:04:23**we update the code, we update the spec.

**00:04:24**They they're they're in lock step. And

**00:04:26**in fact, I stress that in this last

**00:04:27**point in bold. The spec should always be

**00:04:29**up to date with the code unless the spec

**00:04:30**is explicitly marked as superseded or

**00:04:33**deprecated. Okay. Okay. We've got a

**00:04:36**basic framework for what we're doing. So

**00:04:39**what is the project? We're going to be

**00:04:41**developing a new game based very roughly

**00:04:44**on the uh classic uh arcade game

**00:04:46**Asteroids. If that seems a little

**00:04:48**familiar to you, it's because we've done

**00:04:49**it several times on this channel. But

**00:04:50**this time, this time is going to be

**00:04:52**very, very different. This is a very,

**00:04:54**very ambitious undertaking. Uh, I'm

**00:04:57**going to add a ton of stuff to it. It's

**00:04:58**not just a an outer space shoot them up.

**00:05:00**I'm going to turn it into a strategy

**00:05:01**game with a story behind it. We're going

**00:05:02**to have uh the player starting with a

**00:05:04**single very poorly equipped spaceship.

**00:05:06**You go into a dangerous asteroid field

**00:05:08**to to mine precious minerals out of

**00:05:10**these asteroids. And then you collect

**00:05:11**the minerals uh bring them back to a

**00:05:13**space station and sell them. you can uh

**00:05:15**use the money to buy upgrade parts of

**00:05:16**your ship or hire crew that will uh

**00:05:19**increase your ship's stats. Uh and

**00:05:21**eventually you can buy a new ship, a

**00:05:22**better ship, and eventually you can buy

**00:05:23**more than one ship. You can have a fleet

**00:05:25**uh as you set up a mining operation and

**00:05:27**you're competing uh with with other

**00:05:29**mining operations who are trying to

**00:05:31**sabotage your efforts the entire way

**00:05:32**through the game. Uh and the goal is to

**00:05:34**become the dominant mining operation uh

**00:05:37**in the system. So, the name of the game

**00:05:39**is Dust to Dominion. I kind of like that

**00:05:42**name. I hope it's not taken. I haven't

**00:05:43**checked, but it's literal and it's

**00:05:45**poetic at the same time. You start with

**00:05:46**nothing but dust that you collect from

**00:05:47**asteroids and you turn it into Dominion.

**00:05:49**I like it. Okay, so here's my very rough

**00:05:52**first pass. In this first episode, we're

**00:05:55**not going to end up with a playable

**00:05:56**game. It's going to take a long time to

**00:05:57**get there. Uh, and this is a boring

**00:05:59**process, which is why I kind of

**00:06:00**hesitated to make this video. It was

**00:06:02**requested. Uh, people wanted to see an

**00:06:04**example of this back and forth, back and

**00:06:05**forth to see what the process looks

**00:06:07**like. So, this is good. This is as good

**00:06:09**an example as any. Uh let's let's go

**00:06:11**with it since I'm just starting up this

**00:06:12**project anyway. So we start with 00 doc

**00:06:15**is the project overview. This is uh the

**00:06:17**shortest of the docs we're going to

**00:06:18**write. This is also the only doc that

**00:06:20**we're ever going to write that will not

**00:06:21**directly result in code. This is just to

**00:06:24**give the LLM a little bit of grounding

**00:06:25**as to what this project is about. So I

**00:06:27**give a one paragraph uh description of

**00:06:29**the game. Uh there's two main game

**00:06:31**modes, the tactical view and the

**00:06:33**strategic view. And then uh I talk a

**00:06:35**little bit about logging. We're going to

**00:06:36**use Log Guru. And I'm going to talk a

**00:06:38**little bit about testing. We're going to

**00:06:39**have a fully hermetic test suite. Uh by

**00:06:42**hermetic I mean like it shouldn't be

**00:06:44**influenced by uh the environment of the

**00:06:46**system that you're running the test

**00:06:47**suite on. I should be able to run it on

**00:06:48**my laptop or on the server or on a CI/CD

**00:06:52**machine like GitHub or whatever. The

**00:06:53**test suite should uh should not care

**00:06:54**where it's run. It should have a

**00:06:55**completely sealed uh environment of its

**00:06:57**own with mocks and stubs and all of that

**00:06:59**good stuff. And the two docs we're going

**00:07:01**to look at today in this episode are

**00:07:04**kind of dull, but we're we're setting

**00:07:06**the framework of the of the game itself.

**00:07:08**So, we're going to look at application

**00:07:09**configuration. This is setting up a

**00:07:11**module that we can use to load and save

**00:07:14**uh configuration properties for the

**00:07:15**game. I want this to be generic.

**00:07:16**Nothing's going to be hard-coded. I just

**00:07:18**want a generic uh configuration

**00:07:20**loader/saver,

**00:07:22**which I'm describing here. And then I'm

**00:07:24**going to describe the uh the handling of

**00:07:25**the main window. We can play in windowed

**00:07:28**mode uh or in full screen mode where

**00:07:30**with a certain number of supported

**00:07:31**resolutions and uh there's configuration

**00:07:32**properties to drive that. So, these docs

**00:07:34**are very skeletal uh by design because I

**00:07:37**want to I want to show off the process.

**00:07:38**So, I've written these docs. This is

**00:07:39**kind of a little bit halfass. Uh that's

**00:07:42**okay. Uh I'm going to take it to Quen

**00:07:45**and we're going to do this back and

**00:07:46**forth and we're going to refine it both

**00:07:48**of these docs until we've got them in a

**00:07:50**state that uh is pretty good. So, okay.

**00:07:53**So, we go to Pi. The very first prompt

**00:07:56**looks something like this. We're

**00:07:57**starting a brand new project. Take a

**00:07:59**look at the initial docs that I have in

**00:08:00**the docs directory. These are very rough

**00:08:01**and they need expanding before we can

**00:08:03**begin implementing. Please give a review

**00:08:05**of these docs and your thoughts as to

**00:08:06**how they can be improved. This is a very

**00:08:07**very generic prompt. This is our

**00:08:09**starting point. It's going to get a lot

**00:08:10**more sophisticated as we go through

**00:08:11**this. Uh and in fact, I'd like to end up

**00:08:14**with a skill, a document review skill

**00:08:16**that contains all of the things that I

**00:08:17**wanted to look for and specific feedback

**00:08:19**that I wanted to offer uh in a specific

**00:08:21**format. And we're going to get fancier

**00:08:22**as we go. But for this first pass, this

**00:08:24**is just really plain jane simple. I'm

**00:08:26**just giving it this little blur here.

**00:08:27**Take a look at these docs and tell me

**00:08:29**what you think. And uh let's see what uh

**00:08:31**Quinn has to say. So Quinn read the

**00:08:33**docs, thought about it for a good long

**00:08:34**while and came up with if we keep

**00:08:37**scrolling down uh looks like this.

**00:08:39**Review complete. The skeleton is sound.

**00:08:41**The spec driven workflow is exactly

**00:08:43**right for an LLM implemented project.

**00:08:44**And specs one and two show good

**00:08:46**instincts. I get the sense that it's uh

**00:08:49**trying to soften me up or something

**00:08:50**here. Uh let's look for the butt. But

**00:08:52**but at 760 words, this is a table of

**00:08:55**contents, not a spec suite. Fair enough.

**00:08:57**Fair enough. Uh so the docs answer what

**00:09:00**is the game in one paragraph but none of

**00:09:02**the loadbearing questions for

**00:09:03**implementation, how the code is

**00:09:05**structured, what an error is, what

**00:09:06**hermetic means, how specs evolved. Uh

**00:09:08**three concrete defects worth flagging

**00:09:10**before we expand anything. So right off

**00:09:12**the bat I'm getting good valuable

**00:09:13**feedback as to how we can expand uh

**00:09:15**these docs. So let's take a look at

**00:09:17**this. Spec one contradicts itself across

**00:09:19**two layers. Resolution if supported is

**00:09:22**doing undefined work. Let's look at what

**00:09:24**I said exactly for resolution window

**00:09:26**handling. So, I I gave it a very quick

**00:09:27**blur. There there's a windowed mode by

**00:09:29**default. The game displays in a

**00:09:31**non-resizable fixed size window at 1280

**00:09:33**x 720. But if you're in full screen

**00:09:34**mode, the game displays full screen at a

**00:09:36**configurable configurable resolution if

**00:09:38**supported. What I meant, this is a

**00:09:40**little little parenthetical.

**00:09:42**What I meant was if the display that

**00:09:44**we're running on supports these

**00:09:45**resolutions, then we should support

**00:09:47**these resolutions. That I Yeah. So Quinn

**00:09:49**immediately raises the question uh what

**00:09:52**happens if uh 1920 x 1080 is known but

**00:09:56**the display doesn't support it. Do we

**00:09:58**fall back to windowed mode? Do we exit?

**00:10:01**And the display in your example is a

**00:10:02**string. Was it? Oh yeah, it was too.

**00:10:05**Okay. So little stupid things like this.

**00:10:07**Uh coin is is great at zero zeroing in

**00:10:09**on these. I give a specific JSON example

**00:10:11**that was kind of dumb and it immediately

**00:10:14**flags me on it. And uh hermetic is

**00:10:16**loadbearing but undefined. This is good

**00:10:18**feedback. I said I wanted a hermetic

**00:10:20**test suite, but I didn't define that. I

**00:10:22**thought it was obvious. So, uh, Quinn is

**00:10:24**telling me, "No, no, no. It's not

**00:10:25**obvious what you mean by that. This is

**00:10:26**not a footnote.

**00:10:28**[laughter]

**00:10:30**Do we want a dummy video driver? Do we

**00:10:31**want a dummy audio driver? Do we want no

**00:10:33**real file system? Do we want no wall

**00:10:35**clock? Do we want to inject a time? Do

**00:10:37**we CDRG?" Yeah. Okay. Yeah. All valid

**00:10:41**points. All valid points. So, it does

**00:10:43**highlight the things that I got right.

**00:10:45**One tiny little sentence. And then the

**00:10:46**things that I did not get. so good. Uh,

**00:10:49**a big bullet list of changes and then

**00:10:51**again, same pattern. I I got one tiny

**00:10:53**little sentence of here's what you did

**00:10:54**good and then here's what you did not so

**00:10:56**good. Okay, so it's getting ahead of the

**00:10:58**game here. It's it's anticipating that

**00:10:59**we're going to be specking out

**00:11:00**everything. And we are uh but it wants

**00:11:03**to start with everything up front, the

**00:11:06**economy and strategic view like money,

**00:11:08**upgrades, hiring, and fleet. Yeah, okay.

**00:11:10**I Yes, that all needs to be specked out,

**00:11:12**but we're starting with the framework,

**00:11:13**right? So immediately it's going to the

**00:11:17**most niggly of of niggly details. It

**00:11:18**wants to spec out the entire game. And I

**00:11:20**I don't blame it. Uh but sometimes we've

**00:11:24**I I I've got to pump the brakes and say,

**00:11:25**"Look, we're just focused on at this

**00:11:27**point, we're just focused on uh

**00:11:29**configuration and setting up the game

**00:11:31**window. That's it." Okay. So, it offers

**00:11:34**to produce an improved draft. And you

**00:11:37**can do this. You can let the LLM uh

**00:11:39**implement its own suggestions. But uh if

**00:11:41**I let it do that, it's going to try to

**00:11:42**spec out the entire game. and I I want

**00:11:44**to pump the brakes and I want to say no

**00:11:45**no no no no no no we're just focused on

**00:11:47**a couple of things here uh one thing at

**00:11:49**a time right I want to start with the

**00:11:51**basic basic basic frameworks and then

**00:11:53**then we get into all of the uh other

**00:11:55**nitty-gritty so what I prefer to do is

**00:11:58**I'll take its feedback and I'll make a

**00:12:00**second pass at these docs expand them

**00:12:01**where where needed and uh clarify my

**00:12:03**wording I've got you know little

**00:12:05**parentheticals that seemed obvious to me

**00:12:06**when I wrote them they're not obvious

**00:12:07**I'm going to split that out into a its

**00:12:09**own paragraph to define what I mean by

**00:12:10**that improve my examples And uh and then

**00:12:13**we do it again. So uh let's cut here and

**00:12:17**I'll I'll make those changes.

**00:12:19**Okay, that took a lot longer than I was

**00:12:21**expecting it to, but that's okay. Stage

**00:12:22**two, where we do this back and forth is

**00:12:24**always the most timeconsuming stage, and

**00:12:26**it should be this is where we want to

**00:12:27**spend the time. This is the most

**00:12:28**important part of the of the process. We

**00:12:31**want to make sure the document is locked

**00:12:33**down. And we do this by bouncing it back

**00:12:34**and forth. So, I've gone through it uh

**00:12:36**from Quinn's suggestions and also from

**00:12:38**my own eyeballing it and realizing that

**00:12:40**the uh the job that I did on the first

**00:12:42**pass was really pretty skeletal. So,

**00:12:44**let's look at uh maybe not go through it

**00:12:46**line by line. Let's look at roughly what

**00:12:48**I changed here. The initial intro in the

**00:12:51**uh read me is still the same, but I

**00:12:52**added a document format section. Uh

**00:12:54**we're going to use uh YAML front matter

**00:12:56**to uh describe the state of the document

**00:12:59**proposed, active or superseded. Just to

**00:13:00**formalize that a little bit, I give an

**00:13:02**example of what that might look like if

**00:13:03**a document has been superseded. We say

**00:13:05**what document has superseded it. That's

**00:13:07**great. Uh we also list some uh strongly

**00:13:10**recommended sections. Every document

**00:13:11**should have a configuration section, a

**00:13:13**testing section, and an acceptance

**00:13:14**criteria section to make the documents

**00:13:16**themselves testable. This helps the LLM

**00:13:18**in understanding uh whether or not is it

**00:13:21**has hit the target when it's

**00:13:22**implementing one of these one of these

**00:13:23**uh spec docs. And I mentioned that uh a

**00:13:26**spec can have an open question section

**00:13:28**uh when it's in the proposed state.

**00:13:30**That's fine because we're bouncing it

**00:13:31**back and forth. And these are the the

**00:13:32**the questions that we that we've noted.

**00:13:34**Uh but before we move to active meaning

**00:13:37**the uh document is ready to be

**00:13:39**implemented then uh we should have

**00:13:40**resolved all of those questions or

**00:13:42**removed them. If we look at the project

**00:13:44**overview doc I added a little YAML front

**00:13:45**matter section. This is a proposal. Uh I

**00:13:49**think most of this is the same until we

**00:13:51**get to testing. I describe now a little

**00:13:54**bit more about what I mean by having a

**00:13:55**fully hermetic test suite. I want a

**00:13:57**fully simulated environment with dummy

**00:13:59**video and audio drivers, seated RNG, and

**00:14:01**an injected clock so we can have

**00:14:02**deterministic behavior so that uh the

**00:14:04**unit tests can test every specific thing

**00:14:06**that happens in the game in a fully

**00:14:08**sealed environment. Uh I'm also

**00:14:10**describing a little bit more about the

**00:14:11**uh project architecture, the specific

**00:14:13**dependencies we're going to pull in, the

**00:14:15**rough uh top level uh you know uh

**00:14:17**directory structure of the game. Uh

**00:14:19**frame rate is an important thing to lock

**00:14:20**down. Persistence, we're going to assume

**00:14:22**specific uh known directory unless

**00:14:24**otherwise noted. and uh road map. I know

**00:14:27**Quinn is eager to get the entire road

**00:14:29**map laid down, but I I'm going to just

**00:14:30**say cuz this is a spare time project.

**00:14:32**I'm just going to say there is no

**00:14:33**project schedule, Quinn. We're not, you

**00:14:35**know, trying to hit, you know, specific

**00:14:36**milestones by specific dates. This is

**00:14:37**not a commercial product. I'm not trying

**00:14:39**to make money off of this thing. I just

**00:14:40**want to have fun making a fun game and I

**00:14:42**don't care if it takes 6 weeks or 6

**00:14:44**months or 6 years for that matter. If we

**00:14:46**look at application config, I basically

**00:14:47**rewrote the document to greatly uh

**00:14:50**enhance the uh the details that are in

**00:14:52**here. Uh Quen specifically called out

**00:14:53**the fact that I said uh a number of

**00:14:55**times I said it should raise an error if

**00:14:57**this happens or it should raise an error

**00:14:58**if that happens. Well, what error? What

**00:14:59**what specific error do you want to

**00:15:01**raise? Uh validation error, invalid

**00:15:03**config error, missing config error. We

**00:15:05**should we should define this up front

**00:15:06**otherwise the LLM might get confused and

**00:15:08**invent its own uh thing. So we want to

**00:15:10**try to you know again guide it as

**00:15:11**closely as we can. Uh reading and

**00:15:14**writing made a note about atomic uh file

**00:15:17**rights. Uh versioning is out of scope.

**00:15:19**uh game configuration. Uh the game

**00:15:21**itself is a client of the config module.

**00:15:23**So I'm just describing what that looks

**00:15:25**like and uh how we're going to access

**00:15:27**the main game config file. And then unit

**00:15:29**test. I I specified more detail about

**00:15:31**what specifically we're going to be like

**00:15:32**uh testing. And then acceptance criteria

**00:15:34**for this document is this. We should be

**00:15:36**able to load and uh and save

**00:15:38**configuration. Great. And then window

**00:15:41**handling. Again, basically uh greatly

**00:15:43**expanding the details. Uh what's the

**00:15:45**fallback if a resolution that's

**00:15:46**requested is not supported? Uh can we

**00:15:48**toggle at runtime between uh windowed

**00:15:50**mode and full screen mode? Yeah, that's

**00:15:52**a good idea. Configuration. I'm adding a

**00:15:53**little bit more details about uh the

**00:15:55**configuration that is going to drive

**00:15:56**these uh these modes. And then uh unit

**00:15:59**testing section there at the end. Okay.

**00:16:02**So I think we're ready for pass two. And

**00:16:03**I don't expect that it's going to pass

**00:16:05**with flying colors, but we're going to

**00:16:06**get closer every time we do this. So I'm

**00:16:08**going to go back to PI in the same

**00:16:11**session because we've only used 30,000

**00:16:13**tokens of context, which is nothing.

**00:16:15**Okay. I've updated all docs with more

**00:16:17**detail and better wording. Take another

**00:16:19**look. We're still not ready to write

**00:16:20**code. I have to keep stressing to Quen

**00:16:22**that we're not we're not going to dive

**00:16:23**into the code. I know Quinn is dying to

**00:16:24**do that. Uh just give the docs another

**00:16:26**look and let me know if there's still

**00:16:27**ambiguities or fine points that we need

**00:16:29**to nail down and Quinn can have another

**00:16:30**look at uh all of the docs. Okay, what

**00:16:34**do we get? Significant improvement, but

**00:16:37**some ambiguities. Spec to contradict

**00:16:39**itself on mode. Does it? Oh jeez, it

**00:16:42**does. Okay, so I've got a contradiction

**00:16:44**in uh config handling. I've got uh I

**00:16:48**didn't specify an error case quite

**00:16:49**correctly. It's finding edge cases in

**00:16:51**the config writing. Uh that's good. It's

**00:16:54**raising questions about the full screen

**00:16:55**toggle. Do we save that immediately?

**00:16:57**Yeah, we should probably mention that

**00:16:59**there's error cases. What if you try to

**00:17:01**hit F11 on a mode that's not supported?

**00:17:04**Yeah. Okay. So, all the little niggly

**00:17:06**details, edge cases and such. Great.

**00:17:08**Okay. It's concerned because I'm

**00:17:10**referencing environment variables that

**00:17:11**don't exist yet, but they'll be in

**00:17:13**future documents. Quinn again wants to

**00:17:16**see the entire spec for the entire game

**00:17:17**all at once. And I I I I don't have it.

**00:17:19**You know, we're not there yet. [snorts]

**00:17:21**And then a whole bunch of typos and uh

**00:17:24**grammar failures. That's I'm useless

**00:17:27**after 10 p.m. apparently. That's fine.

**00:17:29**Can fix those. So uh all right, six

**00:17:32**decisions we have to make and then 13

**00:17:33**mechanical fixes. Great. So we're

**00:17:35**narrowing it down. So we do another

**00:17:38**pass. Now this is what pass three. By

**00:17:40**the way, for the purpose of this

**00:17:41**exercise, I'm committing every uh stage

**00:17:44**of this so that it's in it'll be in the

**00:17:46**commit history of this uh of this

**00:17:48**project. I wouldn't normally bother uh

**00:17:50**if we do the back and forth dance three

**00:17:52**or four, five, six times, I would just

**00:17:54**commit the end result of that uh because

**00:17:55**I don't really care about the

**00:17:56**intermediate steps as we're refining it.

**00:17:58**Uh it's not really useful. It just

**00:17:59**clutters the git commit history. But for

**00:18:01**this example, as we go through it, I'm

**00:18:02**going to checkpoint commit every stage.

**00:18:05**every time I I do uh another round of

**00:18:07**review with Quen, I'm going to

**00:18:09**checkpoint commit uh what I'm sending to

**00:18:10**Quen so that we can, you know, review it

**00:18:12**later if it's interesting. So, let's cut

**00:18:15**again and uh let me uh let's go for pass

**00:18:18**number three.

**00:18:19**Okay, changes made. Uh I just want to

**00:18:21**point out something that made me laugh

**00:18:23**while I was going through this. Uh I at

**00:18:25**some point in the docs, I wrote uh if

**00:18:27**such and such an error happens, you

**00:18:29**should raise OS error or similar. OS

**00:18:32**error is a standard Python error that

**00:18:34**just means something bad happened when

**00:18:35**interacting with the operating system

**00:18:36**like we couldn't write the file or

**00:18:37**whatever, right? So, I said OS error or

**00:18:40**similar. I I didn't want to lock it into

**00:18:41**a specific exception type. I just, you

**00:18:43**know, like pick something appropriate is

**00:18:44**what I meant. But look at this. Look

**00:18:45**what my code agent said. [snorts] OS

**00:18:48**error or similar. First, the Python name

**00:18:51**is OS error, not OS error. Second,

**00:18:53**you've been using specific exception

**00:18:55**names throughout. Now you're saying or

**00:18:56**similar. That breaks the pattern. Name

**00:18:57**the exact exception. I'm getting a

**00:18:59**little bit of attitude from my coding

**00:19:00**agent. I actually kind of like it. Uh it

**00:19:02**when I say something vague or something

**00:19:04**that could be open to interpretation, it

**00:19:06**slaps me and it says, "No, no, no, no.

**00:19:08**This is a specification. You're supposed

**00:19:09**to be specific. You're supposed to lock

**00:19:11**this stuff down. This is the kind of

**00:19:12**stuff that uh we need to uh specify."

**00:19:15**Okay, so I've committed uh those changes

**00:19:18**I just made. Should we go through them

**00:19:20**really quick? It's getting pretty

**00:19:21**niggly. I don't know if it's worth going

**00:19:22**through it uh statement by statement,

**00:19:24**but uh basically I just expanded uh a

**00:19:27**few points. I I addressed its concerns.

**00:19:29**Uh, its main concerns were that I I

**00:19:31**wasn't being specific enough and there

**00:19:33**were still a few things that weren't

**00:19:34**locked down properly and the test suite

**00:19:35**is a little bit vague and uh, a little,

**00:19:38**you know, little this or that. This is

**00:19:39**the kind of stuff that made me hesitant

**00:19:41**to make this video cuz this doesn't show

**00:19:43**very well. This is not a very

**00:19:44**entertaining video in my opinion. Uh,

**00:19:46**let's go with it. Let's uh, go back to

**00:19:50**Quen and say, "Okay, I've once again

**00:19:53**updated the docs per your suggestions. I

**00:19:54**think it's getting closer now, but don't

**00:19:56**start on the code just yet. I want to

**00:19:57**stress that every time I talk to it uh

**00:19:59**until we're absolutely sure. Let's make

**00:20:01**sure we've got it all locked down

**00:20:02**sufficiently and go. I'm still in the

**00:20:04**same session. We're only up to 45,000

**00:20:06**tokens of context. So, we got plenty of

**00:20:07**context left. These documents aren't

**00:20:09**huge. I mean, I they've grown since pass

**00:20:12**one, but they're still not terribly

**00:20:13**huge. So, I I think we're okay. Let's

**00:20:15**just let Quinn think about that. Okay.

**00:20:17**What do we get? Four contradictions,

**00:20:19**three guaranteed misbehavior gaps, and

**00:20:21**eight fine points. So, it feels like

**00:20:23**we're trimming it down. You know, we're

**00:20:25**getting closer. And you might think,

**00:20:27**this has been what, hour? Hour and a

**00:20:28**half. I should have timed it. Uh you

**00:20:30**might think if you're going to spend an

**00:20:31**hour and a half refining the document,

**00:20:32**you could have written the code in that

**00:20:33**amount of time. Well, uh sure, but think

**00:20:36**about it. When you jump into the code,

**00:20:38**these are the kind of things you got to

**00:20:39**lock down anyway. These aren't throwaway

**00:20:41**documents that we're generating here.

**00:20:42**These uh documents that we're generating

**00:20:45**are for the initial implementation, but

**00:20:47**also going forward. This is a record of

**00:20:49**the architectural decisions that we've

**00:20:50**made on the project, and they will

**00:20:51**influence future documents. And uh

**00:20:54**because these documents contain uh

**00:20:56**testing steps and acceptance criteria,

**00:20:57**we can we can validate against them. Uh

**00:21:00**that uh the the code that was

**00:21:02**implemented actually fulfills these

**00:21:03**specifications.

**00:21:05**And when we need to make changes to that

**00:21:06**code, we can uh redo that validation

**00:21:08**process to make sure we haven't broken

**00:21:10**anything, to make sure we haven't

**00:21:10**deviated from the spec anywhere, uh or

**00:21:13**to find areas where the spec needs to be

**00:21:14**updated to reflect the changes that

**00:21:16**we've made to the code over time. So

**00:21:18**once again, let's cut and uh we'll go

**00:21:20**for passport.

**00:21:22**Okay, that only took 10 minutes. So, uh

**00:21:24**I think it's getting to the point where

**00:21:26**maybe maybe we're ready. Uh [snorts]

**00:21:29**let's try this again. This is what past

**00:21:31**4

**00:21:32**past 5. I've lost I've lost track. It's

**00:21:34**late. It's very late. Let's uh let's

**00:21:37**keep going. Okay, documents have been

**00:21:39**updated. Give them one more look and if

**00:21:41**we're ready to implement, let me know.

**00:21:44**Uh if you still see things that need

**00:21:46**tightening down, otherwise uh I'll do a

**00:21:47**checkpoint commit and final review. So,

**00:21:49**go. We're still not writing code just

**00:21:50**yet.

**00:21:51**Uh, we're going to let Quen take a look

**00:21:53**at these changes. I have actually

**00:21:57**already done the checkpoint commit.

**00:21:58**Let's just do a get log. So, 1 2 3. So,

**00:22:02**yeah, this is the fourth pass. Niggly

**00:22:03**details. Now, this, by the way, this is

**00:22:06**this is typical. This is what the

**00:22:07**process looks like. It's not it's never

**00:22:08**a one-shot deal. The spec dock always

**00:22:11**has got stuff that uh needs tightening

**00:22:13**down on the first pass, first two, three

**00:22:14**passes. Four, five, six passes is not

**00:22:16**unusual. You want to make sure it's

**00:22:18**locked down. That's the whole point of

**00:22:19**this section. It's it's it's repetitive.

**00:22:23**Uh it's maybe not thrilling, but it's

**00:22:26**important to lock it down. Even for

**00:22:28**simple stuff like this, we're just

**00:22:29**setting the framework. We haven't even

**00:22:30**described the game yet. We're just

**00:22:32**describing the framework that's uh going

**00:22:33**into the game and standing up this uh

**00:22:35**document system for this for this new

**00:22:36**project. Uh this may seem like a lot of

**00:22:39**back and forth, and it is, but it's it's

**00:22:40**important stuff. The good news is that

**00:22:43**as we get down to the really niggly

**00:22:45**details, I can stop patching it myself.

**00:22:47**I can just say, "Okay, if I've still got

**00:22:49**typos or grammar mistakes in there, you

**00:22:50**you can just fix them. I can just tell

**00:22:51**Quinn to to to deal with the the niggly

**00:22:54**stuff." Okay, what did we get? Uh,

**00:22:57**fourth pass is complete. Verdict is

**00:22:59**converged, but not quite locked.

**00:23:03**Uh, so we fixed a bunch of stuff, but we

**00:23:04**still have some stuff that needs

**00:23:05**tightening. Uh, spec 01 was only half

**00:23:09**fixed. I'm still referencing an old uh

**00:23:12**error that can't be thrown in that case.

**00:23:13**Okay. Okay. Stale keys remain in place

**00:23:16**and are validated but ignored as false

**00:23:18**in two ways. Okay, so I haven't locked

**00:23:20**that down. Wording. It's It's nitpicking

**00:23:23**my wording. I said native resolution. I

**00:23:25**meant current resolution. It's important

**00:23:26**to get stuff like that right. I get it.

**00:23:28**I get it. It's late. It's late. Quen,

**00:23:31**give me a break. Okay, easy changes.

**00:23:34**Okay, that was fast. That was like 5

**00:23:35**minutes. Let me just checkpoint commit

**00:23:37**that. I think it's ready cuz those were

**00:23:39**all very small changes. So, I'm going to

**00:23:41**give Quen now the option uh to actually

**00:23:44**implement. Okay, I've made the edits you

**00:23:46**suggested. Give it another read. And if

**00:23:48**you're happy with it, we I say we can

**00:23:50**proceed with implementation. Really,

**00:23:52**it's you know, Quinn Quinn can proceed

**00:23:54**with implementation and mark these

**00:23:55**documents as active once finished and

**00:23:57**go. Uh I think I think this should be

**00:24:02**it. Let's see what Quinn says. Okay.

**00:24:05**Okay. Quen's happy. It's jumping into

**00:24:08**the code. So, at this point, I can go

**00:24:11**hands off. I can go do something else.

**00:24:13**Maybe get ready for bed. It's after

**00:24:14**midnight now. Uh, and, uh, Quinn can

**00:24:17**just continue with implementation. So,

**00:24:19**I'm going to cut there cuz Quinn's going

**00:24:21**to take probably an hour to work through

**00:24:22**this. And, uh, we'll we'll see what we

**00:24:23**end up with.

**00:24:25**Okay, implementation complete in 37

**00:24:28**minutes. That's actually not bad. It

**00:24:29**compacted once. What I should have done

**00:24:31**in hindsight is start a new session

**00:24:34**because that's the whole point, right?

**00:24:35**Once we've locked down the spec, we

**00:24:36**don't have to stay in the same session

**00:24:38**where it was uh refining the spec with

**00:24:40**me, we can start a brand new session and

**00:24:42**just point it at those docs and say,

**00:24:44**"Okay, here's your specification. Go for

**00:24:46**it." Uh, I stayed in the same session. I

**00:24:48**I blame it on the fact that it's way

**00:24:49**past my bedtime and it ran out of

**00:24:51**context and it had to compact. So, it

**00:24:53**took a little bit longer than it should

**00:24:54**have, but still uh not bad. So, let's uh

**00:24:56**let's see what we got. Uh it's reporting

**00:24:58**that there's 55 tests that it added. Uh

**00:25:01**it implemented spec 0, one, and two.

**00:25:04**So, basically, we've got application

**00:25:06**config and we've got a uh skeletal main

**00:25:08**window that should be able to go full

**00:25:09**screen and uh and not I had to ask it to

**00:25:12**give me a a main.

**00:25:15**Let's uh see.

**00:25:19**Okay,

**00:25:21**I got a window. If I hit F11, it should

**00:25:24**go full screen. It was on the wrong

**00:25:25**monitor, but okay, it works. [snorts] It

**00:25:29**does go full screen, but on the other

**00:25:30**display, uh I should be able to

**00:25:32**configure that actually.

**00:25:34**It should have just created a and it did

**00:25:36**dust to do Dominion directory in my home

**00:25:39**directory. And it should have created

**00:25:40**and it did a game.json.

**00:25:43**So I should be able to start it in full

**00:25:46**screen on display one.

**00:25:50**Invalid config. Okay. Well, it gave me a

**00:25:53**uh Okay. So yeah, it really wants to go

**00:25:55**onto the laptop display, not the

**00:25:57**external monitor, but okay. So what what

**00:25:59**we end up with after that what was that?

**00:26:01**2 hours of documentation refinement. We

**00:26:03**get an empty window. [laughter]

**00:26:07**We get an empty window. I can go full

**00:26:10**screen and I can come out of full screen

**00:26:11**and we've got a config file that in

**00:26:14**theory I can edit and that's it. This is

**00:26:16**why I didn't want to do this episode

**00:26:18**when the requests came in. Uh oh, I'd

**00:26:20**love to see a working session of your

**00:26:22**specriven vibe coding technique. No, you

**00:26:24**wouldn't cuz it [laughter] looks like

**00:26:25**this. We don't have we don't have

**00:26:28**anything to show for it at the end

**00:26:29**really. This is a long process. This is

**00:26:32**uh going to take weeks. I can't show it

**00:26:34**in a single video, or if I did, it would

**00:26:35**be a montage video of footage that I

**00:26:37**collect over multiple sessions, over

**00:26:39**multiple days and weeks to put together

**00:26:41**the whole game. It's going to take a

**00:26:42**long time. This technique shines when

**00:26:45**you have a large ambitious project that

**00:26:48**you can't oneshot. When we do the battle

**00:26:49**episodes on this channel or uh an LLM uh

**00:26:52**review episode on this channel, I try to

**00:26:54**come up with a tailored prompt that is

**00:26:57**complicated enough to be challenging but

**00:26:59**still doable in a single episode, right?

**00:27:01**We we can conceivably give it to an LLM

**00:27:04**and it can produce something for us, a

**00:27:05**working game or uh a spinning uh sprite

**00:27:08**animator or a color changing uh dialogue

**00:27:11**or you know something something that we

**00:27:12**can show uh cuz YouTube is a visual

**00:27:14**medium, right? You want to see stuff.

**00:27:16**this process that we just did, 2 hours

**00:27:18**of refining a document to come up with

**00:27:20**the skeletal scaffolding of a of a new

**00:27:23**game. This is not compelling content

**00:27:26**unless you're really into uh development

**00:27:29**methodologies.

**00:27:30**This is not oneshot yolo prompting. This

**00:27:32**is this is not vibe coding in the

**00:27:34**strictest sense. This is driving it from

**00:27:36**a detailed specification. And the

**00:27:38**majority of my time is spent in that

**00:27:40**back and forth dance where we refine

**00:27:42**even simple prompts, even simple specs

**00:27:44**like the ones we looked at tonight. I

**00:27:45**just want application configuration and

**00:27:47**I want a main window that can go to full

**00:27:49**screen and back. That's it. Those those

**00:27:51**those seem like they should be really

**00:27:53**simple specs. But if you look at them,

**00:27:55**I'll put these all on my GitHub. Uh

**00:27:57**including the the uh change history of

**00:27:59**the what was it? Four passes we made,

**00:28:01**five passes, I can't remember of the

**00:28:02**four or five passes of the documentation

**00:28:04**or finer process that we made and the

**00:28:06**final imple implementation that we got

**00:28:07**out of Quinn just now. I'll put it all

**00:28:08**on my GitHub. You can look at it. You

**00:28:11**can see where I started with the really

**00:28:13**naive the the halfbaked uh uh spec docs

**00:28:16**that I came up with two hours ago. Uh

**00:28:19**compare them to the spec docks that we

**00:28:21**ended with after pass 4 or past 5 or

**00:28:22**whatever the last pass was. You'll see

**00:28:25**the level of detail that is required to

**00:28:28**to make this technique really shine. And

**00:28:30**it it takes time to get there. The end

**00:28:32**results here is maybe not super

**00:28:34**impressive, but it's about laying a

**00:28:37**foundation that we can build on. And uh

**00:28:39**this is proper this is closer to proper

**00:28:41**software development than uh certainly

**00:28:43**closer than the oneshot yolo prompt

**00:28:45**technique that we do in a battle episode

**00:28:46**where I just say, "Okay, build me a

**00:28:48**build me an asteroids game." Uh this is

**00:28:51**a much more ambitious project.

**00:28:52**Ambitious. Ambitious. Okay, I can't

**00:28:54**speak anymore. So, let's let's call it

**00:28:56**there. Okay, cut to the next day. It got

**00:28:59**kind of late last night. Uh by the time

**00:29:01**Quinn was done coding, it was way past

**00:29:03**my bedtime. So, uh I didn't show the

**00:29:05**last couple of steps. So, here's a

**00:29:07**little addendum to show this because uh

**00:29:09**this is uh this is critical to the

**00:29:11**process. No matter how tight your your

**00:29:13**spec is, uh no matter how tightly you

**00:29:15**think you've locked it down, no matter

**00:29:16**how many back and forth passes you did,

**00:29:17**we can't assume that the first pass of

**00:29:19**implementation is going to be correct.

**00:29:21**We have to verify it. So, how do we do

**00:29:23**that? We can do a little bit uh last

**00:29:24**night, I did a very little bit of manual

**00:29:26**testing, and I saw some wonky behavior

**00:29:28**where it wasn't uh rendering full screen

**00:29:30**on the on the correct monitor. Uh so,

**00:29:32**right away, I know there's there's

**00:29:33**there's little problems here and there.

**00:29:34**Uh [clears throat] but we can go deeper

**00:29:36**than that. Uh I actually do a two-stage

**00:29:38**code review. Uh stage one and take it to

**00:29:40**open code uh with my uh code review

**00:29:43**agent and I let Quen review its own work

**00:29:44**in a fresh session. So it has no memory

**00:29:46**of having written the code. It thinks

**00:29:48**it's looking at somebody else's code for

**00:29:50**for all it knows. So uh I I get a fresh

**00:29:52**code review from Quen and it did find

**00:29:54**the number one bug that it found was

**00:29:56**that F11 to toggle full screen mode is

**00:29:58**ignoring uh what display the windows on.

**00:30:00**It's supposed to, if you drag it to the

**00:30:02**external monitor and hit F11 there, it's

**00:30:04**supposed to go full screen on that

**00:30:05**monitor. So Quinn found that one. Great.

**00:30:08**Uh Quen finds another one uh that if you

**00:30:11**set display in windowed mode, the spec

**00:30:14**says to ignore that. So let's just take

**00:30:16**a quick look at that file. Let me do

**00:30:18**this. If your display, if your mode is

**00:30:21**set to windowed mode, then display is

**00:30:24**supposed to be ignored. According to the

**00:30:26**spec, it should just load in the in the

**00:30:28**primary display always. I wrote that in

**00:30:30**the spec and I can't remember what I was

**00:30:31**thinking when I wrote that because

**00:30:32**that's kind of weird. If I should be

**00:30:33**able to set the display for the window

**00:30:35**to come up in, right? I mean, this is

**00:30:37**wonkiness in the spec. So, this is an

**00:30:39**interesting case where we've got two

**00:30:40**examples of what we can find in code

**00:30:42**review. Uh, bug number one is something

**00:30:44**in the code in the implementation that

**00:30:46**was wrong. It doesn't match what the

**00:30:47**spec says. And bug number two, the code

**00:30:49**is technically ignoring the spec, but

**00:30:51**it's doing the smart thing. The spec

**00:30:53**says to do it the dumb way, and the code

**00:30:54**actually does it the smart way. I think

**00:30:56**we should backport that into the spec.

**00:30:58**we should update up update the spec

**00:31:00**based on what the code uh says. This is

**00:31:02**actually a common uh scenario even in

**00:31:05**real world software development. You get

**00:31:06**into the implementation of something

**00:31:08**based on a spec and you find that the

**00:31:09**spec is kind of wonky. You look at it

**00:31:11**and it's like wait a minute, why do you

**00:31:12**want me to do it that way? Wouldn't it

**00:31:13**be better to do it this way? And so you

**00:31:15**you write the code a different way than

**00:31:16**what the spec says and then you

**00:31:17**retroactively update the spec to reflect

**00:31:19**reality because the spec was wrong. The

**00:31:21**spec wasn't wrong. It was just kind of

**00:31:23**weird. It was it's weird that I wrote it

**00:31:24**that way and I don't know what I was

**00:31:26**thinking. Uh it also found a couple of

**00:31:28**edge cases where if uh a bad error

**00:31:30**happens during config save, it can crash

**00:31:32**the game. There's a minor problem with

**00:31:34**config merging. If you're uh up saving

**00:31:36**updated config to the uh to the JSON

**00:31:38**file, so fine.

**00:31:40**Now, I mentioned that I do a two-stage

**00:31:42**code review. I don't rely entirely on

**00:31:43**Quen because Quen might miss stuff. It

**00:31:46**has happened. Uh case in point, I I

**00:31:48**still have a GitHub Copilot personal

**00:31:49**account. So I uh I use Copilot as my uh

**00:31:52**fallback code reviewer. and Copilot

**00:31:54**found that we're doing something that

**00:31:55**might not work on Windows. There is an

**00:31:57**uh OS.get EUID call that might not be

**00:32:01**available on Windows. I can't test this

**00:32:02**cuz I don't have a Windows machine. So,

**00:32:04**I'll take Copilot's word for it, but uh

**00:32:06**Copilot flagged it in two places that uh

**00:32:08**this might crash if you run the test

**00:32:09**suite on Windows. Okay. Uh I'll take

**00:32:12**that back to Quinn and we'll adjust it.

**00:32:14**So, so I've got a list of bugs supplied

**00:32:17**by Quinn. I've got a that one bug found

**00:32:19**by GitHub Copilot. So, a good example

**00:32:21**of, uh, why it's important to have more

**00:32:23**than one code reviewer. And, of course,

**00:32:25**manual hands-on testing, which I did a

**00:32:26**little bit of that last night, and I

**00:32:27**noticed right away that, okay, if I set

**00:32:29**this in the config file and start it up,

**00:32:31**uh, it doesn't seem to be working

**00:32:32**correctly. Uh, and it isn't. So, uh,

**00:32:35**good. So, even on simple spec docs like

**00:32:37**this, uh, we can still easily run into

**00:32:40**problems in implementation. So, we have

**00:32:41**to check it. Okay, let me cut there.

**00:32:44**I'll get Quinn to fix all of these bugs

**00:32:45**and then we'll show the finished product

**00:32:47**such as it is. and uh and then we'll

**00:32:49**we'll we'll recap. Okay, that took a

**00:32:51**while, but uh we got it. We got all of

**00:32:53**the bugs. Not only the ones that Copilot

**00:32:55**found, but the ones that Quen found. And

**00:32:57**uh I think it's all working now. So, if

**00:32:59**I start it up, it'll start on monitor

**00:33:04**zero. If I drag it over to monitor one

**00:33:06**and hit F11, it goes full screen there.

**00:33:08**If I drag it back to monitor zero, hit

**00:33:10**F11, it goes full screen there. If I

**00:33:13**drag it so it's mostly on this window,

**00:33:15**but a little bit on the other window,

**00:33:16**it'll go full screen here. And if I drag

**00:33:19**it so it's mostly on that display and

**00:33:21**not on this display, it'll go full

**00:33:22**screen there. Perfect. All right. So,

**00:33:25**all of that just to get our first couple

**00:33:27**of spec docs in place and to get the

**00:33:29**framework of this game in place. And all

**00:33:30**we get to show for it is a blank window

**00:33:32**with nothing in it. We can't play

**00:33:33**anything. We can't interact with it. We

**00:33:35**we can just I mean, it comes up, it

**00:33:38**reads the config, and it displays a

**00:33:40**window. Boring, boring stuff. But we're

**00:33:42**laying a base and as we advance forward,

**00:33:45**every new feature that we roll out for

**00:33:47**this thing is backed by a very tightly

**00:33:50**locked down spec. The spec is not

**00:33:52**written in stone. Uh we can backport

**00:33:54**stuff into the spec. If we find stuff as

**00:33:56**we did in this implementation round, we

**00:33:58**find that the implementation is actually

**00:33:59**a little bit better than what the spec

**00:34:00**said. So, okay, let's fix the spec to

**00:34:02**match what we coded. What we coded. I

**00:34:04**keep saying we, it's Quen doing the

**00:34:06**coding. Uh but the point is we uh

**00:34:08**everything's well considered.

**00:34:09**Everything's driven by a spec. We're not

**00:34:11**just vibe coding. We're not just

**00:34:12**throwing stuff at the LLM and hoping

**00:34:13**that it figures it out. Uh everything is

**00:34:15**uh very well considered. Okay, that was

**00:34:18**a long way to go over two evenings just

**00:34:19**to get a couple of spec docks in place.

**00:34:21**But now that we've laid the base, now

**00:34:22**that we've laid the foundation, I think

**00:34:24**uh I think progress will move a little

**00:34:26**bit more rapidly now. But this gives you

**00:34:27**a flavor of what the process looks like

**00:34:29**since you since you asked since we're

**00:34:30**among a number of comments on that

**00:34:31**episode about I'd like to see this

**00:34:33**process in action. Well, there it is.

**00:34:34**It's it's dry and it's dull and you

**00:34:36**don't always get something flashy to

**00:34:38**display at the end of it, but uh it

**00:34:40**works. It's a solid process. I really

**00:34:41**like it and uh it works well. It works

**00:34:43**well in my in my project. So there you

**00:34:45**go. Uh

**00:34:48**I hope you enjoyed that one. Uh feel

**00:34:50**free to join our Discord and come by and

**00:34:51**have a chat if you want. And uh the next

**00:34:53**time we see this project on this

**00:34:56**channel, it'll be a lot flashier than uh

**00:34:58**than what we just saw. Hopefully. All

**00:35:00**right, that will do it for today. We

**00:35:02**will see you next time.

**00:35:06**A couple of episodes ago, I did

**00:35:09**something. Three, two, one.

**00:35:11**A couple of episodes ago, I talked about

**00:35:12**a development methodology that I've been

**00:35:14**using in my own project, and I tongue

**00:35:15**and cheek called it vibe coded. What did

**00:35:18**I call it? Vibe coding something or

**00:35:20**other. Uh, that'll do it for today. It

**00:35:22**is way past my bedtime. Thank you for

**00:35:23**watching, and we will see you next time.

**00:35:27**Is anybody going to watch that?

**00:35:29**[laughter]

**00:35:31**Even I wouldn't watch that. And I just

**00:35:32**lived it.
