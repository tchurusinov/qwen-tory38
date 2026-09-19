---
id: yt-jZ9Mdit5TdI
name: QWEN 3.8 27B Local AI Review
description: "Qwen 3.8 27B Review, Prompts and vLLM Settings https://digitalspaceport.com/qwen-3-8-27b-review-prompts-and-vllm-settings/\n\nThreadripper WRX80 Build\nMOBO - WRX80  \nAsrock Creator https://geni.us/ASRock_wrx80_creator\nGigabyte MC62-G40 https://geni.us/MC62-G40_MOBO\n\nCPU  - AMD Threadripper PRO\n3945WX "
license: CC-BY-4.0
sources:
- https://www.youtube.com/watch?v=jZ9Mdit5TdI
tags:
- youtube
- channel-digital-spaceport
- captions-auto-generated
classifications:
  category: video-transcript
  platform: youtube
  source_type: auto-generated
video_id: jZ9Mdit5TdI
channel_name: Digital Spaceport
view_count: 81697
upload_date: '20260814'
caption_track_type: auto-generated
segment_count: 441

---

I just produced the first outputs with

**00:00:02**my Quinn 3.8 27B. This is running in

**00:00:06**VLLM. I'm going to go through the script

**00:00:08**that I'm using for the run block, all of

**00:00:09**that stuff, but I wanted to give you a

**00:00:11**look at the first output. It did take 2

**00:00:13**hours and 40 minutes. I had it generate

**00:00:16**an arcade box. We're going to see what

**00:00:18**this looks like here. It does say that

**00:00:20**it is up and running. Let's just see

**00:00:22**what this looks like. So, 192.168.1.215

**00:00:27**8088

**00:00:32**All right. This is looking pretty much

**00:00:35**like I was expecting. So, I gave it a

**00:00:37**prompt that basically told it to create

**00:00:39**three retro games emulator style 8-bit

**00:00:42**synthwave as a feature and

**00:00:46**with sound. And it does appear that the

**00:00:48**sound is working also. So, it should

**00:00:50**have all the things like leaderboard and

**00:00:52**everything else. And this is all in

**00:00:54**JavaScript, nothing in Python. Let's

**00:00:57**start off with Void Raiders, which is

**00:00:59**basically an invasion defense game. Hold

**00:01:02**the line against descending alien grids,

**00:01:03**destructible bunkers, mystery craft

**00:01:05**waves that only get angrier. All right,

**00:01:07**let's see if this plays or not.

**00:01:15**Oh, this is very hard actually.

**00:01:20**Don't shoot your own defenses.

**00:01:26**Ooh.

**00:01:34**Okay. One What? Dang, two dead. Uh-oh.

**00:01:43**Oh, all right. So,

**00:01:45**DSP and my score was 900. So,

**00:01:50**let's see if it saved that to the global

**00:01:52**leaderboard also.

**00:01:53**Okay, cool. So, Void Raiders DSP. So, it

**00:01:56**kind of has a global leaderboard. Let's

**00:01:59**try Nebula Drift.

**00:02:07**Oh, this one's going to be hard, I bet.

**00:02:11**Oh, this one's very hard.

**00:02:14**So, I just got to fly around. No!

**00:02:18**And avoid getting hit by the asteroids.

**00:02:21**Uh this one's

**00:02:23**Okay, that one's

**00:02:26**That one's almost impossible.

**00:02:36**Okay, the enter key being

**00:02:39**a part of the dynamic here. The enter is

**00:02:40**the fire key. So, that makes it very

**00:02:42**hard and space is thrust. So,

**00:02:45**hmm

**00:02:46**that one needed some little bit probably

**00:02:49**refinement on it. This was all a zero

**00:02:51**shot. So, just

**00:02:53**This is just what I was able to come up

**00:02:55**with right off the bat, basically.

**00:03:03**And this was all using Hermes agent as

**00:03:05**well to orchestrate everything. And I've

**00:03:07**got to say,

**00:03:09**the

**00:03:10**Oh, yeah. The quality of the code is

**00:03:13**substantially better

**00:03:15**than what I've seen before. And that

**00:03:19**this is just really good quality though

**00:03:22**for a zero shot. Literally zero shot.

**00:03:25**I did have it do a full review on it. I

**00:03:28**told it to do full review on it. One

**00:03:30**prompt and this was the output. That is

**00:03:33**impressive. And it did it I I know it

**00:03:36**doesn't look like it was very fast here.

**00:03:37**Let's take a look back. It is possibly

**00:03:40**still doing some compaction and I

**00:03:41**there's a lot of things that I'm going

**00:03:43**to talk about, especially with MTP that

**00:03:45**you're going to want to consider here.

**00:03:46**But, I mean, holy cow. Really good.

**00:03:50**Honestly, I am super impressed because

**00:03:53**playable right off the bat. Overall,

**00:03:57**this is better than 3.6 could ever have

**00:03:59**produced, even hooked up to Hermes agent

**00:04:02**on a zero shot. Like,

**00:04:04**very, very much different. Quinn 3.8

**00:04:08**huge difference versus 3.6.

**00:04:10**I saw a zero tool calls fail. Zero tool

**00:04:14**calls fail. Like, if we go up here,

**00:04:17**no tool tool calls failing. Just no tool

**00:04:20**calls failing. By the way, I'm going to

**00:04:22**put this all up so you can access it,

**00:04:24**check it out, play it if you want to, uh

**00:04:27**run your own if you're interested in

**00:04:29**doing that. Full context window fits as

**00:04:32**well. Full context window fits. Hit its

**00:04:34**compaction right at the end of this

**00:04:36**entire generation, which super glad that

**00:04:38**it didn't happen in the middle of it.

**00:04:41**And I think there's yet another game,

**00:04:43**paddle breaker, that I did not play yet.

**00:04:45**So, it keeps best score. It has kind of

**00:04:48**a running tally of high scores here. It

**00:04:50**is Buzo number five. By the way, do you

**00:04:52**like Buzo number five? Buzo number five

**00:04:55**is four 3090s and one 4090 over there

**00:04:59**connected at full 16 width, and this is

**00:05:02**on the WRX 80 Threadripper platform.

**00:05:06**Definitely, definitely a speed

**00:05:08**improvement I'm noticing already in

**00:05:10**this.

**00:05:11**Let's uh give this one a play here. We

**00:05:13**have not played Starbreaker yet.

**00:05:20**New arrows. Okay.

**00:05:47**That was kind of It went

**00:05:49**up, around, and stuff.

**00:05:52**I'm not sure if I'm supposed to be

**00:05:53**catching those or not.

**00:05:57**I like this dynamic where it bounces off

**00:05:59**the top also, though. That's actually

**00:06:01**quite helpful.

**00:06:09**M does toggle the sound, so that's good.

**00:06:12**P does look like it Oh, okay. So, P does

**00:06:15**not pause. P resets. So, there's a bug.

**00:06:18**That still is pretty damn good for a

**00:06:21**zero shot.

**00:06:22**I mean, this is just fun. This is just a

**00:06:25**lot of fun.

**00:06:26**Let's see.

**00:06:27**Once you mute it also, it does not want

**00:06:30**to appear to come back off of mute while

**00:06:32**you're in it. So, I was in here and I

**00:06:34**muted it.

**00:06:38**Now, let's try going back and see if

**00:06:42**So, there's sound toggling, it looks

**00:06:44**like, on some buttons that is not quite

**00:06:45**working out right. But, yeah, this is uh

**00:06:49**This is really good quality. For Quinn

**00:06:52**3.8 27B, let me mute that so I'm not

**00:06:55**talking over it.

**00:06:58**I don't know if I'm going to be able to

**00:06:59**mute it.

**00:07:01**Oh, okay. So, I'm going to have to mute

**00:07:03**the tab. So, there are some audio

**00:07:05**toggling issues that

**00:07:07**you could go back and have it refined

**00:07:09**here. Terminal bench, the improvements

**00:07:11**that we saw from Quinn 3.6, I have got

**00:07:14**to say are substantial. As a matter of

**00:07:16**fact, like no tool calling, the terminal

**00:07:19**operations just worked every single

**00:07:22**time. Now, this is a brand new fresh

**00:07:24**Hermes installation. So, sometimes

**00:07:27**that's probably the best way to get a

**00:07:28**basis on a agentic install is you can

**00:07:32**bring in a lot of cruft and a lot of

**00:07:34**broken stuff and a lot of stuff that's

**00:07:35**awesome. So, I did a completely fresh

**00:07:37**Hermes installation. This was, of

**00:07:39**course, in our Proxmox instance over

**00:07:41**here, and this is in DSP Hermes that

**00:07:43**I've got this up and running.

**00:07:44**Definitely, it feels like this is

**00:07:46**insanely better code quality. The code

**00:07:48**that I was able to generate with 3.6 27B

**00:07:51**was very much iterative back and forth,

**00:07:54**fix things, and this is through Hermes,

**00:07:56**fix things, review things, things are

**00:07:58**still broken that it should have caught,

**00:08:01**inability to use things like Playwright

**00:08:03**really succinctly was another thing that

**00:08:05**3.6 had.

**00:08:07**3.8 nailed Playwright just insanely

**00:08:10**well. So,

**00:08:12**I am incredibly impressed, incredibly,

**00:08:15**incredibly impressed with the

**00:08:16**improvement. As well, it did tons of

**00:08:19**vision that I told it that was part of

**00:08:21**the instructions was use your vision

**00:08:23**skills. So, I basically started up and

**00:08:25**primed it by giving it the giganto text

**00:08:28**file and saying, "You're using Hermes

**00:08:30**agent." And I said, "You're also running

**00:08:32**Qwen 3.8 27B. Fully review both Hermes

**00:08:36**agent's document and 3.8 27B. Understand

**00:08:39**fully your capabilities and utilize your

**00:08:41**capabilities in the development of this

**00:08:43**game." Which is a very good way to start

**00:08:45**off an agentic session if you are using

**00:08:47**a new brain in it and it doesn't have

**00:08:49**that kind of requisite knowledge

**00:08:51**already. That was it. That was basically

**00:08:53**it. And I'll post the prompt and

**00:08:55**everything like that at

**00:08:56**digitalspaceport.com as well as all the

**00:08:58**rest of this, and you can find a link to

**00:09:00**that in the description below. But, this

**00:09:02**is just a really great first look at

**00:09:05**this, and I am really, really impressed

**00:09:08**at the output that we were able to get

**00:09:09**from it. I'm going to be playing with it

**00:09:11**more this weekend. I can guarantee

**00:09:12**there'll be more videos. We're almost 4

**00:09:15**hours in, and this is I can tell already

**00:09:19**it is the improvement that we were

**00:09:21**really hoping for. I mean, the tool

**00:09:23**calling especially just being almost

**00:09:25**flawless on it, the ability for the full

**00:09:27**context window to fit in, excellent,

**00:09:30**excellent, excellent. So, a couple

**00:09:31**things here, let me quickly show you.

**00:09:33**We'll terminate out of our VLLM instance

**00:09:37**here, and let me go ahead and cat our

**00:09:39**run block.

**00:09:40**So, if you want to, you can copy this

**00:09:43**run block. I would recommend disabling

**00:09:46**speculative config. As a matter of fact,

**00:09:47**I'm going to do that right now for

**00:09:49**myself. So, instead of cat, we'll just

**00:09:51**go to nano.

**00:09:55**And I did not think that the speed up

**00:09:59**that I was seeing was and the workload

**00:10:02**really wasn't like if you're doing code

**00:10:04**review and stuff like that, working on

**00:10:05**the same project over and over, you're

**00:10:07**going to really see MTP kind of start to

**00:10:09**shine there. But, looking through this,

**00:10:11**I did actually go back and change some

**00:10:13**things that in the prior 3.6, do not use

**00:10:16**your 3.6 run block that I showed you how

**00:10:18**to basically get some really optimum

**00:10:20**tuning if you have quad 3090s or quad

**00:10:23**GPUs in general that this will fit on.

**00:10:25**Don't don't reuse that. I'll I'll show

**00:10:27**you the things that are different here,

**00:10:29**but there are a couple things that are

**00:10:31**different. In particular, the enable

**00:10:33**thinking is now enabled and true and

**00:10:36**functioning. Prior in 3.6, you really

**00:10:39**did have to disable this for really good

**00:10:41**agentic workflows to not just have

**00:10:44**really bad loops, all sorts of problems

**00:10:46**that would happen in them. So, we've got

**00:10:48**our tensor parallel, of course, at four.

**00:10:50**The max model length was set to auto.

**00:10:52**Hermes does a great job of auto

**00:10:54**detecting that. Fully pulled it in at

**00:10:56**262 144 256K. The GPU memory

**00:11:00**utilization, I bumped this from 0.90 to

**00:11:03**0.93. It worked fine, and that maybe

**00:11:06**even could go up a little bit higher

**00:11:08**like 0.95. I did not see that there was

**00:11:10**context run out or anything like that.

**00:11:12**And especially with I think I will

**00:11:14**actually push that. Especially with MTP

**00:11:17**disabled, that's a little more space for

**00:11:18**the KV cache. So, that should actually

**00:11:20**work out just fine. Port 9876, our host

**00:11:24**is 0.0.0.0,

**00:11:26**and a couple other The rest of this

**00:11:28**looks the same. MM process cache type

**00:11:30**SHM.

**00:11:32**We've got our max num batch tokens at

**00:11:34**8192. That will give you big fat

**00:11:36**batches. Enable prefix caching. Mamba

**00:11:39**cash mode align. And our API key. Our

**00:11:42**CUDA visible devices are 1 2 0 and 4.

**00:11:45**Good to explicitly set your GPU order.

**00:11:48**I've seen that in Quint 3.6. So, I made

**00:11:50**sure to copy that over here. The The

**00:11:53**order could probably be changed around,

**00:11:54**but setting an order is what I would

**00:11:56**recommend. Our op num threads we set to

**00:11:58**four. That's a very good recommendation

**00:12:00**for you. So, that means you would

**00:12:02**probably be wanting to run your vLLM

**00:12:04**instance on like an eight core kind of

**00:12:06**count in a virtualization instance or a

**00:12:10**eight core minimum CPU. And the ML MKL

**00:12:14**num threads is set to four also. Our CU

**00:12:17**mem enable is disabled. And we also have

**00:12:21**our expandable segments true. Like if

**00:12:23**you're using multi GPUs, you definitely

**00:12:24**want that. And also you won't actually

**00:12:26**be able to set your CUDA visible device

**00:12:28**order until you actually set this as the

**00:12:31**PCIE bus ID up here. So, if you want to

**00:12:33**copy a run block that really works good,

**00:12:36**here's your shortcut also to running

**00:12:37**this. It does occupy pretty much almost

**00:12:41**all of your

**00:12:43**memory. So, let me save that out. Reset

**00:12:45**that. And let's

**00:12:48**hit that run block again. And we'll jump

**00:12:50**back into Hermes really quick. And I'll

**00:12:52**ask it if it can save a skill based off

**00:12:55**of what it just did. Which I think it

**00:12:57**did as such an excellent job that I

**00:12:59**definitely would want this skill saved

**00:13:01**to my own internal repository of skills.

**00:13:03**Again, when you're setting things up

**00:13:05**like this, I know I've mentioned it

**00:13:06**several times, use Giddy internally for

**00:13:09**your own Git repository. It is an

**00:13:11**excellent way. Give your agent its own

**00:13:14**credential set so that it can make its

**00:13:16**own commits to it. And that will give

**00:13:18**you a much better insight and a much

**00:13:20**better rollback point if there is

**00:13:22**problems on things that it is working

**00:13:24**on. You can even have it create things

**00:13:26**for its own skills. You can have it

**00:13:27**update to its own skills in there as

**00:13:29**well. Yeah, you can see the speed of

**00:13:31**loading also dramatically faster on the

**00:13:34**Threadripper. 4 GHz versus 3.325

**00:13:38**max single stream and it went down from

**00:13:41**there versus almost all the threads are

**00:13:43**running at four. I think all the threads

**00:13:45**are running at four. On the 3945WX,

**00:13:48**that video that I did, when I did that,

**00:13:49**I did pick up one of the 3945WX's.

**00:13:52**I already had a WRX80 motherboard

**00:13:54**though, so I just reuse that and I sold

**00:13:56**my 5955WX,

**00:13:58**which not that big of a difference in

**00:14:01**performance that I can see between the

**00:14:02**3945WX and the 5955WX

**00:14:06**in what I'm doing right here. Also, keep

**00:14:08**in mind, this actual desktop, this

**00:14:10**recording that's being done right now is

**00:14:11**all happening in my Casio last VM

**00:14:14**instance that is right here and our

**00:14:16**Hermes instance also is living on this.

**00:14:19**And then we'll get a better idea of what

**00:14:20**the actual tokens per second looks like

**00:14:22**as it's creating the skill.

**00:14:24**Of course, we've got our NVTop over

**00:14:25**here.

**00:14:27**Buzzo number five. Buzzo number five is

**00:14:29**alive.

**00:14:31**Do you get the reference? Maybe.

**00:14:33**Got to be old to get that reference.

**00:14:35**Do like the new rig though.

**00:14:37**Insanely heavy. Insanely heavy with all

**00:14:40**the GPUs in it and dual power supplies,

**00:14:43**a 1500 and 1000. That entire build video

**00:14:46**is going to be a really long build

**00:14:48**video. I give so much meta in that and

**00:14:50**that is coming out probably sometime

**00:14:53**very, very soon. We've been working on

**00:14:55**that one, but I did film it while we

**00:14:57**were putting it together out here

**00:14:58**sweating to the oldies in the garage

**00:15:00**because have not gotten a mini split

**00:15:03**system yet, but the good news is I did

**00:15:05**have to turn it off for recording this.

**00:15:07**The little mini AC, since we're able to

**00:15:09**confine all this in this area, has been

**00:15:12**okay enough to keep things not super

**00:15:15**hot. But definitely

**00:15:17**definitely need a mini split. Definitely

**00:15:19**going to go with a mini split. And

**00:15:21**thanks for everybody that dropped the

**00:15:22**recommendations. I did read a lot of

**00:15:23**those. And we are started up here now,

**00:15:25**so let's get back over to the terminal.

**00:15:32**And you can see we are parked at right

**00:15:33**about 94% of memory utilization, 23

**00:15:37**gibibytes or so on each one of the GPUs

**00:15:41**over here. And

**00:15:42**I'm just going to tell it, yes, please

**00:15:45**create that skill.

**00:15:48**And I'll move that skill over as soon as

**00:15:50**we

**00:15:51**connect it back up over here

**00:15:54**to its own giddy

**00:15:56**credentials. I haven't taken the time

**00:15:58**yet to give this brand new Hermes

**00:15:59**instance giddy credentials and stuff

**00:16:01**yet.

**00:16:02**So yeah, you can see it now, 100% on

**00:16:05**this. Definitely have one of the GPUs

**00:16:07**here. Since I'm using NVLink on two of

**00:16:10**these, that pushes two of them together

**00:16:12**closer. That's always going to make one

**00:16:14**of them hotter, usually the one that is

**00:16:16**kind of

**00:16:17**on the bottom of the connection. So fan

**00:16:20**number one, this one here is going to be

**00:16:22**hotter on that NVLink cuz there's just

**00:16:24**not much space for the air to get in

**00:16:26**there, and it's basically pulling heat

**00:16:28**off of the back plate of the other one.

**00:16:30**So, whether or not I keep the NVLink on

**00:16:33**it or not is a big question. May end up

**00:16:35**selling the NVLink, I don't know. I'm

**00:16:36**going to test the NVLink out, play

**00:16:38**around with it a little bit, and we'll

**00:16:40**see. I guess I'll just say that, we'll

**00:16:41**see. Oh, you can hear it spinning up

**00:16:43**also over there now.

**00:16:46**So prompt throughput at 7,214 tokens a

**00:16:49**second, gen at 15 currently, and that

**00:16:52**should get a little bit more refinement

**00:16:54**as it starts running through. So now

**00:16:56**we're seeing the gen coming in at about

**00:16:57**41.2. So

**00:17:00**that's pretty much where you're going to

**00:17:02**expect to be on a quad 3090 rig.

**00:17:06**But seriously,

**00:17:08**super hats off to the Alibaba Cloud team

**00:17:11**that produced this.

**00:17:13**Excellent.

**00:17:14**This This This is

**00:17:16**This is amazing. This is amazing work.

**00:17:19**Definitely there is a FPA and a NVFP4.

**00:17:22**Those are essentially quants also. But

**00:17:24**if you are maybe running Blackwell GPUs

**00:17:27**that NVFP4, if you got two 5060 TI 16 GB

**00:17:31**GPUs, 32 GB, probably can fit that in.

**00:17:35**If you've got two 4090s or something

**00:17:37**like that, you probably can run that FP8

**00:17:40**and get

**00:17:41**pretty similar performance. I'm not sure

**00:17:42**what the quality loss is going to look

**00:17:44**like amongst those. So, at any rate,

**00:17:46**check that out digitalspaceport.com.

**00:17:48**Link to the prompt, link to also

**00:17:51**probably the playable if you want to

**00:17:53**play this also, will be in the

**00:17:54**description below. And thank you

**00:17:56**everybody that signs up for the members

**00:17:58**on YouTube.

**00:18:00**It really has helped out the channel

**00:18:01**quite a bit. And if you're interested in

**00:18:04**learning more about how you can get up

**00:18:06**and running and building rigs that are

**00:18:07**doing things like this, well first off,

**00:18:10**the updated build video for this is

**00:18:12**coming. And man, this is a great build.

**00:18:15**This is just

**00:18:17**This is a great build. But you can find

**00:18:18**out more in the playlist here.

**00:18:24**Have a great rest of your day.
