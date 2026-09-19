---
id: yt-975ILFNTKfk
name: Is Bonsai 2 27B REALLY 98% of Qwen 3.8 27B fp16?
description: 'We evaluate inference speed (~68–70 tokens/sec) of Ternary Bonsai 2 27B and put its agentic coding capabilities to the test with Hermes Agent to generate a full arcade game suite—comparing the results head-to-head against full fp16 Qwen 3.8 27B.


  🔧 Guides & Links:

  • GPU Pricing & Specs Tracker: http'
license: CC-BY-4.0
sources:
- https://www.youtube.com/watch?v=975ILFNTKfk
tags:
- youtube
- channel-digital-spaceport
- captions-auto-generated
classifications:
  category: video-transcript
  platform: youtube
  source_type: auto-generated
video_id: 975ILFNTKfk
channel_name: Digital Spaceport
view_count: 57798
upload_date: '20260918'
caption_track_type: auto-generated
segment_count: 407

---

You don't have a lot of VRAM, but you

**00:00:01**want the best model possible. One of the

**00:00:03**biggest questions I get on the channel

**00:00:05**is what can I run in just 1 3090 or

**00:00:07**maybe a couple of 3060s. Today we're

**00:00:10**going to test this out and we're going

**00:00:11**to be using the ternary bonsai 227B.

**00:00:14**Now, if you're not familiar, ternary

**00:00:16**means -1 0 and positive 1 as

**00:00:19**representation states instead of just -1

**00:00:22**and positive 1. This should give a

**00:00:24**little bit better quality to the smaller

**00:00:27**size that we're looking at here, which

**00:00:28**is nine times smaller, so it can fit 24

**00:00:31**GB of VRAM, the full 27B with a very,

**00:00:36**very minimal loss in quality, so they

**00:00:38**claim. We're going to put this to the

**00:00:39**test today, however. So, we're going to

**00:00:41**jump right in. You I'll I'll put the

**00:00:43**link. You can learn more about all of

**00:00:44**this stuff, but I know everybody's like

**00:00:46**let's get to the meat and potatoes here.

**00:00:48**So, I'll just run you through the steps

**00:00:49**to get this up and running. I'm going to

**00:00:51**assume that you have followed along with

**00:00:53**the guide on digital spaceport, which is

**00:00:55**the Proxmox 9 LXC guide. That's what I'm

**00:00:57**going to be using here over on Procs 2

**00:00:59**and we're going to be running in

**00:01:00**container 118. So, I've already SSH'd

**00:01:03**into this container. We're going to

**00:01:05**paste this in

**00:01:06**and run it. This should go ahead. It's

**00:01:09**going to ask for your hugging face key

**00:01:11**if you want to give it that. I'm just

**00:01:12**going to skip it cuz this is a small

**00:01:13**model. It'll still download very fast.

**00:01:15**But basically, this should be able,

**00:01:18**hopefully, with this demo to run a

**00:01:21**really high quality with amazing

**00:01:24**retention of precision at really, I

**00:01:27**hope, decent speeds. See, speed has been

**00:01:30**one of the biggest problems for people

**00:01:31**running, you know, smaller GPUs and

**00:01:34**sparks and stuff like that is that is a

**00:01:36**pretty big impact that you have as far

**00:01:38**as running a dense like this. So, if we

**00:01:40**can run this and get decent speeds on

**00:01:42**it, it really opens up a new frontier

**00:01:43**possibly for people who have 1 3090 or

**00:01:46**maybe 2 3060s even. And today I tossed

**00:01:50**in 2 3060 12 GB, so we're also going to

**00:01:52**get a speed test on those. We're going

**00:01:54**to do the in-depth testing on the single

**00:01:56**3090, however. And if you're interested

**00:01:58**in what the prices look like, you can

**00:02:00**find those out on our new feature on

**00:02:02**digitalspaceport.com.

**00:02:04**And my wife is updating this quite

**00:02:05**frequently. We are seeing $1,400 as the

**00:02:08**lowest used price right now for 3090s.

**00:02:10**That is up, actually, over the past

**00:02:12**couple days quite substantially. Of

**00:02:13**course, 5090s have gone through the roof

**00:02:16**this past week, and 4090s and 3090s are

**00:02:19**kind of following in that trail. But, I

**00:02:21**will say the 3060 we've got down here is

**00:02:24**$300 the lowest used price for that

**00:02:26**right now. And so, we're going to test

**00:02:27**that one out also, see what the kind of

**00:02:28**performance is. You can find that in the

**00:02:30**links below. That is uh

**00:02:32**digitalspaceport.com/gpus

**00:02:35**to get there, also.

**00:02:36**So, let's see if it's got that all

**00:02:38**downloaded yet.

**00:02:40**Okay, so it's got it all downloaded.

**00:02:42**Perfect. The next thing that we're going

**00:02:43**to do is pull up one of the commands

**00:02:46**from their get repo. So, there's a

**00:02:48**couple things you want to check out

**00:02:49**here. So, like the environment variables

**00:02:51**is something you want to pay attention

**00:02:52**to. So, we're going to use bonsai host

**00:02:54**as a variable to override the local

**00:02:57**host. Now, if you're running this on a

**00:02:58**machine and not serving it over your own

**00:03:00**local network, you can just leave it as

**00:03:02**local host, and that'll be fine. But,

**00:03:04**we're going to override the local host

**00:03:06**address here when we set ours up and

**00:03:07**bind it to 0.0.0.0. That means we'll be

**00:03:10**able to access it from any location on

**00:03:12**the internal network here. As well,

**00:03:13**there's speculative, the KV cache set to

**00:03:16**four if you need to really crunch it

**00:03:17**down, and a context size. I'm going to

**00:03:19**leave it at auto for the context size,

**00:03:21**and I'm not going to set speculative or

**00:03:22**KV4 for this test. And we're going to

**00:03:25**give it kind of the same test that we

**00:03:26**have seen with this exact same model

**00:03:28**when we tried it at full precision from

**00:03:30**Quinn. So, we're going to CD into, it

**00:03:33**looks like, scripts.

**00:03:36**And yeah, that's it over there. So,

**00:03:39**you've got start llama server.sh. So,

**00:03:42**get our override in there.

**00:03:51**And that should get us up and running

**00:03:53**here and we can see our 3090 down here.

**00:03:55**Hopefully we get that loaded up just

**00:03:57**really quickly and we'll take a look at

**00:03:59**what the memory footprint looks like as

**00:04:01**we're running this also here. So it

**00:04:02**looks like it is loaded up. It is

**00:04:04**currently occupying 16.772

**00:04:07**GB worth of space on the GPU of the 24

**00:04:10**GB that it has available. So that gives

**00:04:13**quite a bit of extra headroom there. It

**00:04:15**should be accessible also on

**00:04:16**192.168.1.61

**00:04:19**and I believe this is going to be port

**00:04:21**8080 is the default. Okay, so we can see

**00:04:24**that it is loaded up.

**00:04:26**Howdy and see what kind of performance

**00:04:28**we're getting. Okay, so like 69 tokens

**00:04:30**per second is what we're seeing right

**00:04:31**off the bat there.

**00:04:41**And we're going to give it a very

**00:04:42**open-ended make an animated SVG of a cat

**00:04:44**walking on a fence at night and I told

**00:04:45**it to make it awesome. You can see we're

**00:04:47**holding pretty steady here at about 69

**00:04:50**tokens per second. Very much faster than

**00:04:52**what we would be looking at if we were

**00:04:54**of course this is decode. If we were

**00:04:56**looking at running the full 27 bit FP16

**00:04:59**which would be about 35 to maybe 40 with

**00:05:02**four 3090s running at the same time on

**00:05:05**that NVLLM. Of course what we're running

**00:05:07**here is llama.cpp. This does not have

**00:05:10**upstream support yet integrated into the

**00:05:12**core of llama.cpp for right So for right

**00:05:15**now you need to run their fork that you

**00:05:17**can grab just like I'm grabbing here to

**00:05:20**be able to get outputs from it. Still

**00:05:23**holding at about 68 tokens a second.

**00:05:25**Okay, let's see how much context it gave

**00:05:27**us. So it set our context at 131. That

**00:05:30**probably won't be a problem, but

**00:05:32**definitely if you're running agentic

**00:05:34**workflows you might want to go higher

**00:05:35**than that. If you're doing something

**00:05:36**like Hermes agent, setting that up a

**00:05:38**little bit higher especially at these

**00:05:40**speeds. I don't know what the prefill's

**00:05:41**going to look like, but we'll get to

**00:05:43**checking out what the prefill looks like

**00:05:44**pretty soon here. I'll have it maybe do

**00:05:46**another revision or something like that.

**00:05:48**Should retain vision support. Like I

**00:05:50**mentioned, it does have the Dflash

**00:05:52**speculative decoder also built into it.

**00:05:54**So, it should be for certain types of

**00:05:56**tasks really, really good. If you're

**00:05:58**doing like structured code and stuff

**00:05:59**like that or math, it should be actually

**00:06:01**pretty darn decent at going through that

**00:06:04**and getting a very serious speed

**00:06:06**increase.

**00:06:30**Okay.

**00:06:32**That's a bad fence, but the cat walking

**00:06:34**across it was actually pretty good.

**00:06:37**>> [laughter]

**00:06:39**>> It's not the best fence that I've ever

**00:06:41**seen. Let's Let's take a look at this

**00:06:42**larger.

**00:06:43**So, I think the cat is one of the best

**00:06:45**cats walking that I've ever seen one of

**00:06:47**these make. There was Okay, the shooting

**00:06:50**star it looks like it's hitting the moon

**00:06:52**like direct impact

**00:06:54**but the fence is pretty bad. The cat

**00:06:56**walking pretty good. Even has a little

**00:06:58**shadow. I really think this is a

**00:07:00**creative expression that we're seeing

**00:07:03**out of it. I would have to say

**00:07:05**I like the cat a lot. I think this is

**00:07:07**actually one of the better ones that

**00:07:08**we've seen, but definitely, definitely

**00:07:11**I'm not sure if this reaches quite the

**00:07:12**same quality as the FP16 would, but it

**00:07:15**did it so fast that you know, mm, I've

**00:07:18**got to say I'm going to save this for

**00:07:20**posterity. Maybe put it up on the

**00:07:21**website on digitalspaceport.com.

**00:07:24**So, yeah, this is

**00:07:28**This guy here and let's just take a look

**00:07:29**at kind of like what it took. So, it

**00:07:31**took 25,000 tokens. It really didn't do

**00:07:34**very much prompt processing at all for

**00:07:36**the most part. Hit about 64.4 tokens per

**00:07:38**second. So, it works pretty good. And

**00:07:41**mean, it is functioning pretty good.

**00:07:43**Let's go ahead and toss up our Hermes

**00:07:45**agent here.

**00:07:50**Okay.

**00:07:55**Definitely is kicked up and started

**00:07:57**thinking now also, it looks like.

**00:08:03**And it looks like the prompt

**00:08:04**processing's kicking in right about

**00:08:06**1,250 tokens per second.

**00:08:09**Okay, so we're going to run this against

**00:08:11**head-to-head what we got out of the

**00:08:13**outputs for Quinn 3.8 27B at FP16, which

**00:08:18**was pretty stellar for the arcade

**00:08:19**results, if you remember that video. And

**00:08:21**if you don't, you have the chance today

**00:08:22**to go play this at

**00:08:23**arcade.digitalspaceport.com,

**00:08:25**and you can head-to-head the versions

**00:08:27**yourself, and you let me know in the

**00:08:28**comments below what you think about the

**00:08:30**two variants. And we're going to have it

**00:08:31**run the full suite that we had generate

**00:08:34**before. This should create a complete

**00:08:37**video game arcade console style, and

**00:08:40**actually have everything up and running

**00:08:42**pretty pretty good as soon as it's done.

**00:08:46**Good luck to it. This is a hard one. It

**00:08:49**took quite a bit of time in the past, so

**00:08:51**let's see.

**00:08:53**I think I need to change the text here,

**00:08:56**cuz that should update. So, I need to

**00:08:58**change that one thing.

**00:09:05**And it is off.

**00:09:10**Okay, so it has been a really really

**00:09:13**long time. It is now the morning. I got

**00:09:16**about four, maybe five hours of very

**00:09:19**light sleep there.

**00:09:21**And this is what we have.

**00:09:23**I would say,

**00:09:25**let's take a look at this versus what

**00:09:28**the full FP16 produced.

**00:09:31**And there is a quality difference here,

**00:09:33**I would say, just in the landing page.

**00:09:35**We've got Neon Liner, Space

**00:09:37**and Pixel Breaker. So, we're going to go

**00:09:40**through play these. It doesn't sound

**00:09:42**like there's an audio track here, which

**00:09:44**I sometimes maybe not that bad of a

**00:09:47**thing. So, we'll we'll figure it out

**00:09:49**here, but let's let's get Neon Lainer

**00:09:52**running. Okay, so there's already kind

**00:09:54**of some font issues here, I would say.

**00:09:58**This pattern is I is crazy. Okay.

**00:10:06**Okay. So,

**00:10:07**pretty simple.

**00:10:10**Shooting the things. I What What game

**00:10:13**was this exactly? I don't know. You tell

**00:10:15**me in the comments below. I would say

**00:10:17**this is definitely not as fun as what we

**00:10:19**ran into with the other one. I'm not

**00:10:21**sure that I can die.

**00:10:24**Hold on a second. Let me see if I get

**00:10:25**hit by stuff if I No.

**00:10:27**Nothing seems to actually harm me here.

**00:10:29**I can't move forward or back, but just

**00:10:31**side to side. Nothing seems to be

**00:10:33**causing any problems, and this is an

**00:10:34**impossible [music] amount of enemies or

**00:10:37**whatever is going on here. Feels like

**00:10:39**there's a few problems.

**00:10:42**Although, now that I've got a faster

**00:10:43**shooting stream, wow, look at me taking

**00:10:45**them out now. The score is very, very

**00:10:47**small up there, but it's in the upper

**00:10:48**left very far.

**00:10:50**16,000 or so.

**00:10:52**I guess I won or something. I guess I've

**00:10:53**got a boss up here.

**00:10:56**The boss is shooting in the wrong

**00:10:58**direction now.

**00:11:00**So, the boss is definitely shooting in

**00:11:01**the wrong direction, it does appear to

**00:11:02**me. So, that is a bit of a fail.

**00:11:07**Not as good. It It's playable. Hitting

**00:11:09**escape here doesn't seem to be able to

**00:11:11**do anything. Got that. So, Neon Lainer,

**00:11:14**I'm going to give it a um

**00:11:16**I'm going to see minus D

**00:11:19**D.

**00:11:20**Then D plus. Uh not really that great.

**00:11:23**Next, let's check out Space Racer.

**00:11:26**Thread and thread the lanes, dodge the

**00:11:28**pack, and burn the boost to carve

**00:11:30**checkpoints out of the void. Near

**00:11:32**misses, Refill Your Turbo. So, maybe

**00:11:34**this will be a better. Hopefully this

**00:11:35**will

**00:11:36**We still have the weird font thing going

**00:11:38**on. So, definitely

**00:11:41**uh not as good. I mean, that's

**00:11:43**definitely not as good.

**00:11:45**Uh-oh.

**00:11:49**Uh

**00:11:50**I think I got killed there.

**00:11:52**Oh, okay.

**00:11:54**This does not have very good movement

**00:11:55**controls.

**00:11:57**Uh-oh.

**00:11:58**Okay.

**00:11:59**So, Lane Racer is pretty impossible to

**00:12:03**play, honestly. So, let's

**00:12:05**give the last one a try here, Pixel

**00:12:07**Breaker.

**00:12:09**So, definitely

**00:12:12**>> [music]

**00:12:13**>> definitely a quality difference.

**00:12:16**That's not looking good up there. I

**00:12:18**don't think that's supposed to be going

**00:12:19**like that.

**00:12:23**No.

**00:12:24**No. I think Pixel Breaker also So, we've

**00:12:27**got two apps in a D plus, essentially,

**00:12:30**uh on the on the challenge here. So, I

**00:12:33**think that is a fairly decent evaluation

**00:12:37**of is this 98% of what we ran into with

**00:12:41**the other one? And I've got to say, I

**00:12:44**really don't feel like it is.

**00:12:46**Granted, there were some things about

**00:12:48**this one that I thought were

**00:12:50**really, you know,

**00:12:57**like hard, but this was very, very true

**00:13:00**to like what I was expecting, kind of.

**00:13:04**The music was kind of like corny, and

**00:13:06**like it

**00:13:07**it was better gameplay. Granted, it was

**00:13:10**not full screen, which I would have

**00:13:11**preferred it had it been a larger screen

**00:13:13**size. Definitely playable, though. And

**00:13:16**if you hit the backspace, it worked. The

**00:13:18**same was true also when you would look

**00:13:21**at

**00:13:22**the Star Breakers. This This was

**00:13:25**actually Okay, let me just miss that

**00:13:27**right off the bat.

**00:13:28**This was actually pretty darn good.

**00:13:31**And

**00:13:32**wow, I'm talking and doing this is not

**00:13:35**working out for me right now. Like I

**00:13:36**said, not a whole lot of sleep. So, even

**00:13:38**when it bounced up there, it didn't get

**00:13:39**trapped. It kind of went bounced around,

**00:13:42**still did the thing that you would

**00:13:43**expect it to do. [music]

**00:13:44**So, much better quality overall.

**00:13:47**The difference between the FP16 [music]

**00:13:49**and what we're seeing. However,

**00:13:52**there are some things that really went

**00:13:53**right with this. As far as the chat as

**00:13:56**far as the cat, I've got to say both of

**00:13:59**those actually went very smoothly. The

**00:14:01**chat speed is very good. The tool

**00:14:03**calling, I did not see miss tool calls.

**00:14:06**So, I thought that was also a really

**00:14:08**positive. On the negative side, the

**00:14:10**quality is definitely

**00:14:13**different. Not as good.

**00:14:15**Definitely not, in my opinion, 98%.

**00:14:19**Is it still really pretty awesome

**00:14:21**though? Yes. Is it substantially faster?

**00:14:24**Well, yes, it is a absolutely

**00:14:26**substantially faster.

**00:14:28**Is it

**00:14:29**potentially something that is able to

**00:14:31**get better?

**00:14:32**That is a good question. Is it something

**00:14:35**usable? Yeah, if you want to have a chat

**00:14:36**and you want to have a single stream

**00:14:38**agentic workflow with something totally

**00:14:40**usable for that. If you want to do

**00:14:42**agentic code development, probably not

**00:14:45**going to be a good time.

**00:14:47**So, that's my feelings on it and I look

**00:14:49**forward to reading yours in the comments

**00:14:51**below. So, huge hat tip to all of our

**00:14:53**channel members and everybody that

**00:14:54**likes, shares, and subscribes. You can

**00:14:57**do that by hitting the button down below

**00:14:58**and thank you everybody for getting us

**00:15:01**across the line. We are past 100,000

**00:15:03**subscribers now. That is a really

**00:15:06**awesome milestone and we've got a lot

**00:15:08**more really fun and great things for you

**00:15:11**in store after that 100,000. Don't think

**00:15:13**we're stopping. And if you're looking

**00:15:15**for more information about getting up

**00:15:17**and running, maybe following along with

**00:15:18**the Quinn 3.8 video that we did in FP16,

**00:15:22**you can check out the video we've got

**00:15:23**here.

**00:15:25**And if you're interested in some

**00:15:26**hardware videos around how you can get

**00:15:28**up and running with your own local LLM,

**00:15:30**check out the videos that we've got

**00:15:32**here.
