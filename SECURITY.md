# Security review

## Reporting a vulnerability

Please do not report security problems in a public issue.

Open a [confidential issue](https://gitlab.com/efferone/notemad/-/issues/new)
and tick the confidentiality checkbox at the bottom of the form before
submitting. Confidential issues are visible only to you and the project
maintainer.

If you would rather not use GitLab, email efferone@proton.me.

I will ack a report within a week. Notemad is maintained by one person
in their spare time, so please allow a reasonable period for a fix before
disclosing publicly.

----


This is a record of a security and correctness review of Notemad carried out in
August 2026, covering the whole codebase, and of everything that was changed as
a result. It is written to be read by anyone, not just by someone who already
knows the code.

An encrypted notes feature was built after this review and removed again before
0.1, to keep the first release small. Nothing in this document refers to it any
more; `docs/deferred/secret-notes.md` covers what it was and why it is not here.

Notemad is a desktop text editor. It has no network code, does not run other
programs, has no login or accounts, and does not build web pages. That rules
out most of the usual list of security problems before you start: there is no
SQL to inject into, no session cookies to steal, no server to attack. What it
does have is a lot of file handling, and that is where everything in this
document lives.

## The short version

Seventeen things were found. Three were serious, four were worth fixing
promptly, and the rest ranged from minor to worth writing down and leaving
alone. Everything that could be fixed has been. The one item that cannot be
fixed yet is the package checksum, which needs the repository to be public
first, and it is listed at the bottom so it does not get forgotten.

The pattern behind nearly all of it was the same. Notemad reads its own files
back at startup, and it was careful about checking the *shape* of what it found
in them (is this a string, is that a list) and careless about the *values*. A
number that was too big, a name with a slash in it, or a date that was not
really a date could all get through and cause trouble further along.

## How the review was done

Every file was read, and every problem below was reproduced before it was
written down, rather than being guessed at from reading the code. The fixes
were then checked the same way: by running the thing and watching what happened.
The test suite went from 117 tests to 134, with new tests covering each fix that
can be tested without a screen.

## What was found and what was done

| # | How bad | Where | What it was | Status |
|---|---|---|---|---|
| 1 | High | session and archive files | A note's name could contain a path, letting Notemad write and delete files outside its own folders | Fixed |
| 2 | High | reading files | Opening a pipe or a device file froze the whole app forever | Fixed |
| 3 | Medium | opening files | No warning before reading a huge file into memory | Fixed |
| 4 | Medium | saving files | Saving a symlink destroyed the link and never touched the real file | Fixed |
| 5 | Medium | startup | A silly window size in the session file stopped the app opening at all | Fixed |
| 6 | Medium | session and archive files | "Not a number" and "Infinity" were accepted as numbers | Fixed |
| 7 | Medium | PKGBUILD | The package does not check that the download is genuine | Cannot fix yet |
| 8 | Low | fonts | A font name could smuggle in extra styling rules | Fixed |
| 9 | Low | searching | Search results could point at the wrong characters | Fixed |
| 10 | Low | saving files | A power cut could still lose the most recent save | Fixed |
| 11 | Low | folder permissions | One Notemad folder was readable by other users | Fixed |
| 12 | Low | archive | Closing an edited file could lose the changes on some setups | Fixed |
| 13 | Low | tabs | The same file could open twice and the two copies fight | Fixed |
| 14 | Low | two copies at once | Two logins can overwrite each other's notes and tab list | Fixed later |
| 15 | Info | find bar | A complicated search expression can hang the window | Not changed |
| 16 | Info | desktop integration | Other programs you run can control Notemad | Not changed |
| 17 | Info | session backups | A second failed start overwrote the first rescue copy | Fixed |

One more problem was found while making these fixes and is included below as
number 18.

## The serious ones, explained

### 1. A note's name could point somewhere else

Every note is stored in a file named after the note's id, something like
`3f9c2a1e-....txt`. Those ids are written down in `session.json` and in the
archive's `index.json`, and read back the next time Notemad starts.

Notemad checked that an id was a piece of text and nothing more. So an id could
be `../../something`, and because that gets glued onto the folder name to make
a file path, the `../..` part walks back up out of the notes folder. Notemad
would then happily create a file somewhere it had no business writing. Worse,
the Delete button in the recovery window would delete a file outside the
archive entirely.

I proved both: a session file with a doctored id made Notemad write a file three
folders above where it should have, and a doctored archive index made the
Delete button erase a file that had nothing to do with Notemad.

This is not something a stranger on the internet can do to you, because those
files are your own and nobody else can read or write them. It matters anyway.
Notemad's one hard rule is that it never writes anywhere outside its own folders
unless you explicitly save, and this broke that rule. It also meant a corrupted
file could cause real damage rather than just being ignored.

**Fixed by** checking every id before it is used, in both files it can come
from. An id must be a plain name with no folders in it. Anything else is dropped
and the app carries on with the rest. The Delete button checks a second time on
its own, because deleting is the only thing Notemad does that cannot be undone.

### 2. Some files never finish being read

Linux has files that are not really files: pipes, and devices like `/dev/zero`.
Reading one of those does not end. Notemad read whatever it was given without
looking, and everything it does happens on the same thread that draws the
window, so opening one meant the window froze, permanently, with no way to
cancel and nothing on screen to explain it.

The only way out was to kill the process. And because Notemad's handler for
being killed politely also runs on that frozen thread, it never got a chance to
save, so anything typed in the previous half second was gone.

You could hit this from the Open dialog, from the command line, or by restoring
a session that had one of these open.

**Fixed by** checking that something is an ordinary file before reading it. A
pipe or a device now gets "Could not read" and nothing else happens. The same
check was added to the search, which reads files too.

### 4. Saving a symlink wrote to the wrong place

A symlink is a file that points at another file. People who keep their config
files in one folder and link to them from elsewhere use them constantly.

When Notemad saved, it wrote to a temporary file and then renamed it over the
target, which is the correct way to save safely. But renaming over a symlink
replaces the link itself. The result was that the link was destroyed, a normal
file appeared in its place holding your text, the real file it used to point at
still had the old contents, and Notemad said "Saved". You would only find out
later, when whatever reads the real file carried on using the old version.

**Fixed by** following the link first, so saving writes to the file the link
points at, and the link survives. This is what every other editor does. If the
link points at something that does not exist yet, the file gets created.

## The rest, briefly

**3. No warning before opening a huge file.** Notemad reads a whole file into
memory before the window responds again, and there was no limit. A 500 MB log
means a frozen window for several seconds and well over a gigabyte of memory.
Now, anything over 100 MB asks first, naming the size, with Cancel and Open
Anyway. Nothing is ever refused: large logs still open, you just get told what
you are in for. Measured on a 116 MB file: about 580 MB of memory once open.

**5. A bad window size stopped the app opening.** The session file remembers how
big the window was. Notemad checked the size was not too small and never checked
it was not absurdly large. A large enough number is rejected outright by the
graphics toolkit, which threw an error at the exact moment the window was being
built, so the app started up, produced no window, and sat there doing nothing.
The only way out was to find and delete `session.json` by hand. The size is now
capped at both ends.

**6. "Not a number" got treated as a number.** JSON is not supposed to allow
`NaN` or `Infinity`, but Python's reader accepts them. One of these in the
session file made the pane divider position meaningless, and one in the archive
index crashed the recovery window as soon as you opened it. Both are now
rejected the same way any other nonsense value is.

**8. A font name could smuggle in styling rules.** The editor font is applied by
building a small stylesheet with the font's name in it. Quotes were stripped out
of the name so it could not break out, but a line break does the same job, and a
name containing one could inject any styling rule it liked. This only matters if
your settings file has been edited by hand or corrupted, and the worst outcome
is a strangely coloured window, but it was a hole and it is closed. Anything
unprintable is now stripped from the name.

**9. Search results could point at the wrong characters.** To search without
caring about capital letters, Notemad lowercased each line before looking. For
a handful of characters, mostly Turkish and Greek, lowercasing changes the
length of the text, which shifted every position after it along by one. Opening
such a result selected the wrong characters. It now matches case-insensitively
without altering the text, so positions stay true.

**10. A power cut could still lose the last save.** Notemad already forced each
file's contents to disk. What it did not do was force the rename that puts the
file in place, so in the worst case the new contents were safely on the disk but
the folder still pointed at the old ones. Now both are flushed.

**11. One folder was readable by other users.** Notemad's folders are set so
only you can read them, but that was only applied to `notes` and `archive`, not
to the `notemad` folder holding them. Nothing inside was exposed, since the
folders within were still locked, but the fix was one line and there was no
reason not to.

**12. Closing an edited file could lose the changes.** When you close a file you
had edited without saving, the unsaved copy is moved into the archive. Moving
was done with a rename, which fails if the two folders are on different disks. A
setup with temporary state on a RAM disk would hit this, and the changes would
be left stranded where nothing would ever look for them again. It now falls back
to a copy when a rename will not do.

**13. The same file could open twice.** Opening `./notes.txt` and
`/home/you/notes.txt` gave two tabs for one file, each with its own unsaved
copy, and saving one would set off a "changed on disk" warning about the other.
Paths are now compared properly, so the second attempt finds the tab that is
already open.

**14. Two copies running at once could overwrite each other.** Left alone at the
time of the review and fixed afterwards. Notemad allows one copy of itself per
desktop session, which does nothing about a second graphical login or a
forwarded display while it is also open locally. Both copies then write the same
session file, the same settings and the same notes. The mild version is that the
last one to close wins the tab list. The serious version is that a note typed in
one copy can be overwritten by the other flushing a buffer it read minutes ago,
which loses work rather than only an arrangement of tabs.

Notemad now takes an exclusive lock on its data directory at startup and a
second copy refuses to start, saying so on screen and naming the process that
has it. The lock is an advisory `flock`, which the kernel drops when a process
ends however it ends, so a crash leaves nothing stale behind and there is no
lock file to clear out by hand. A filesystem with no working locking is treated
as a reason to run unprotected rather than a reason to refuse, since being
unable to lock is a poor excuse for not starting at all.

The cost is that Notemad can no longer be run over a forwarded display while it
is open locally. That was never really working: it was quietly damaging the
session rather than doing something useful.

**The lock is not a security measure, and it is worth saying so plainly**, since
it appears in a security document and could easily be read as one. It guards
against your own second copy, running with your permission, writing over the
first. It defends against nobody. It could not: anyone able to start a second
Notemad against your notes already has read and write access to those files, and
does not need Notemad to use it. `cat`, `cp` and a few lines of any language do
the same job and never look at the lock, because a lock only binds the program
that chooses to check it. It is also confined to one machine. Share the data
directory into a virtual machine or across the network and whether it is honoured
at all depends on that filesystem, which is a further reason not to lean on it.

**17. Session backups overwrote each other.** When the session file cannot be
understood, Notemad moves it aside rather than destroying it. It always used the
same name, so a second bad start wrote over the copy from the first, which was
the one with your tabs in it. Backups are now stamped with the date and time.

**18. A failed save said nothing.** Found while fixing the others. If writing a
note failed, because the disk was full or read only, the error vanished into the
background and nothing was shown. You would carry on typing into a note that
was not being saved, which for an app whose entire promise is that it saves
without being asked is about the worst thing it could do quietly. It now says
so, on screen, straight away.

## What was deliberately left alone

**7. The Arch package does not verify its download.** The `PKGBUILD` has
`sha256sums=('SKIP')`, which means it accepts whatever it downloads without
checking that it is the genuine file. This cannot be fixed yet because the
repository is private and there is no release to take a checksum of. **Before
this project is made public and packaged, run `updpkgsums` to fill in the real
checksum.** Shipping it as it stands would mean anyone who could interfere with
the download could put their own code on a user's machine.

**15. A complicated find expression can hang the window.** The find bar's
regular expression mode can be given an expression that takes effectively
forever on a large note. It is your own search on your own note, and every
editor with regular expressions has this, so it is recorded rather than fixed.

**16. Other programs you run can control Notemad.** Because Notemad registers
itself with the desktop properly, other programs running as you can tell it to
open, quit, or change settings. This is how all modern Linux desktop apps work
and is what makes "only one copy" and "open this file in the running copy"
possible.

It is left alone because anything able to do it could read your notes directly
anyway. A program running as you does not need Notemad's cooperation to open the
files in `~/.local/share/notemad`. The actions Notemad exposes also only cause
things to happen; none of them hands any text back.

**Creating a note when the disk is full.** Related to number 18. Making a new
note writes an empty file immediately, and if that fails, pressing Ctrl+N simply
appears to do nothing. Unlike a failed autosave, no work is lost, so this is
noted rather than changed.

**Restoring a huge file skips the warning.** The size warning in number 3
applies when you open a file. If you had a 500 MB file open when you last quit,
it is reloaded at startup without asking, since you already said yes to it once.

## What changed about how the app behaves

Almost nothing here is visible in day to day use. The fixes above only show
themselves when something was already wrong. Two are exceptions, and both were
agreed before they were made.

**Saving a symlink now updates the real file.** Previously it replaced the link.
This is a change in where your text ends up, and it is the correct one.

**Files over 100 MB ask before opening.** A dialog names the size and offers
Cancel or Open Anyway. Nothing is refused.

There is also one new thing on screen. Notes are normally written out half a
second after you stop typing. For a very large file, copying the whole thing out
that often keeps the disk busy for no good reason, so the delay now grows with
the size of the file, up to a maximum of ten seconds. When that is happening, a
line appears above the note saying so:

> Large file: your changes are saved every 10 seconds, not as you type.

It only appears on files big enough for it to be true, which in practice means
several megabytes and up, and it disappears again on ordinary notes.

## What was checked and found clean

There were no hardcoded passwords, keys or tokens anywhere. Nothing sensitive is
written to the logs. Note contents are never sent anywhere, because there is
nowhere to send them. The files Notemad creates are readable only by you. There
is no encryption in Notemad: your notes are plain text on disk, and anything
that can read your files can read them. The libraries the app depends on are
current and have no known vulnerabilities. The
XML files that define the colour themes carry
none of the declarations that make XML parsing dangerous. Text going into the
parts of the interface that understand formatting is escaped properly, which
was already being done correctly before this review.

## If you find something

This is a personal project. Open an issue on the repository, or if it is
something that should not be public yet, email efferone@proton.me.
