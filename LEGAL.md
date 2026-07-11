# Legal Notice

**This document is general information, not legal advice.** BitDVR is
distributed under the [GNU GPL-3.0](LICENSE), which provides the
software **as-is, without warranty of any kind** (GPL-3.0 §15) and
**disclaims all liability for damages arising from its use** (GPL-3.0
§16). You are solely responsible for how you use it and for compliance
with the laws and agreements that apply to you.

## Intended use: personal fair use

BitDVR is a personal DVR. It is designed for one purpose: recording
content you already have lawful access to, for your own private,
noncommercial viewing, on your own devices, within your own home
network.

That use falls within long-recognized protections under United States
copyright law:

- **Fair use** ([17 U.S.C. § 107](https://www.law.cornell.edu/uscode/text/17/107);
  [Wikipedia: Fair use](https://en.wikipedia.org/wiki/Fair_use)) —
  the statutory framework that permits certain unlicensed uses of
  copyrighted works, weighing purpose, nature, amount, and market
  effect.
- **Time-shifting** ([Wikipedia: Time shifting](https://en.wikipedia.org/wiki/Time_shifting))
  — recording a program you are entitled to watch so you can watch it
  later. The U.S. Supreme Court held this to be fair use in the Betamax
  case (see below), and it is the legal foundation every consumer DVR
  has operated on since.
- **Space-shifting** ([Wikipedia: Ripping — legality](https://en.wikipedia.org/wiki/Ripping#Legality))
  — moving your own lawful copy between your own devices for personal
  playback, recognized by the Ninth Circuit in the Rio MP3 player case
  (see below). This is all BitDVR's transfer step does: it moves your
  recording from your recording machine to your media server, and its
  torrents are trackerless, flagged private, and confined to your LAN
  by design.

## What BitDVR does not do

- **It does not circumvent DRM.** BitDVR drives [OBS Studio](https://obsproject.com/),
  which captures what is rendered to your screen and speakers during
  normal, authorized playback. No encryption or technological
  protection measure is decrypted, bypassed, or removed, which is what
  the DMCA's anti-circumvention provision
  ([17 U.S.C. § 1201](https://www.law.cornell.edu/uscode/text/17/1201))
  prohibits.
- **It does not download or share content.** BitDVR has no ability to
  fetch content from the internet, and its BitTorrent transfer cannot
  reach a public swarm: torrents are created with no trackers and the
  private flag set, and the seed daemon disables DHT, local peer
  discovery, and port mapping. Making recordings available to anyone
  outside your own household is neither supported nor condoned, and is
  precisely what fair use does not cover.

## Your service agreements still apply

Copyright law is not the only thing that governs recording. **Paid
streaming and subscription services (and some free ones) have terms of
service that may prohibit recording, capturing, or retaining their
content**, independent of whether copyright law would allow it.
Violating those terms is a matter between you and the service — it can
mean account suspension or termination regardless of fair use. Review
the terms of any service you record from. BitDVR's authors make no
representation that recording any particular service is permitted.

## Supplemental precedent

The cases below are background for the principles above, with the usual
caveat that only *Sony* and *Aereo* are Supreme Court holdings and none
of them is a guarantee about any particular use.

| Case | What it established |
|---|---|
| [*Sony Corp. v. Universal City Studios*, 464 U.S. 417 (1984)](https://en.wikipedia.org/wiki/Sony_Corp._of_America_v._Universal_City_Studios,_Inc.) ("Betamax") | Home recording of programming for later personal viewing is fair use; making a device with substantial non-infringing uses is not itself infringement. |
| [*RIAA v. Diamond Multimedia*, 180 F.3d 1072 (9th Cir. 1999)](https://en.wikipedia.org/wiki/Recording_Industry_Ass%27n_of_America_v._Diamond_Multimedia_Systems,_Inc.) | Space-shifting your own lawful copies to another personal device is paradigmatic personal use. |
| [*Cartoon Network v. CSC Holdings*, 536 F.3d 121 (2d Cir. 2008)](https://en.wikipedia.org/wiki/Cartoon_Network,_LP_v._CSC_Holdings,_Inc.) ("Cablevision") | A DVR copy stored on remote hardware, made by and played back to the same user, is a private performance, not a public one. |
| [*A&M Records v. Napster*, 239 F.3d 1004 (9th Cir. 2001)](https://en.wikipedia.org/wiki/A%26M_Records,_Inc._v._Napster,_Inc.) / [*MGM v. Grokster*, 545 U.S. 913 (2005)](https://en.wikipedia.org/wiki/MGM_Studios,_Inc._v._Grokster,_Ltd.) | Distributing copies to the public over P2P networks is infringement — the boundary BitDVR's LAN-only, trackerless design exists to stay behind. |
| [*ABC v. Aereo*, 573 U.S. 431 (2014)](https://en.wikipedia.org/wiki/American_Broadcasting_Cos.,_Inc._v._Aereo,_Inc.) | Serving recorded copies to a paying public is a public performance even when each subscriber gets an "individual" copy — personal use stops where an audience beyond your household begins. |

## Summary

Used as designed — recording what you are entitled to watch, for
yourself, kept within your own network — BitDVR's functions sit on the
same legal footing as the VCR and every DVR since. Used outside that
scope — sharing recordings beyond your household, exposing the swarm to
the internet, or recording in violation of a service's terms — that
footing is gone, and that use is yours to answer for, not this
project's. When in doubt, consult a lawyer licensed in your
jurisdiction.
