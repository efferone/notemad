# Secret notes

A note could be marked 'secret' and was encrypted on disk, so that nothing 
outside Notemad could read it (including it's title). The password went 
through Argon2id to build the key and the encryption itself was AES-256-GCM. 
Switching away from the tab locked it, and it came back locked after a restart.

It might come back when I am happy with it.
