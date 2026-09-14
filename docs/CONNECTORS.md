# Connectors: setting up the linked accounts

Written for the owner, doing this once on the desktop that runs ARC.

The Connectors button in the controls opens the sheet. Google Calendar, Gmail,
Drive, Contacts, Telegram and This computer are there already. Spotify,
Outlook, OneDrive, GitHub and Notion each need a one-time setup below: an app
registered with the service, whose **public client id** goes in `.env`. Then
restart ARC, open Connectors and press **Link**.

Never put a real id, token or secret in this file, a commit, a note or an
email. They belong in `.env` only, which git refuses.

Tokens are kept per person in `links/` in the data folder. That folder is
gitignored, is never backed up by self-repair and is never exported. **Unlink**
in the sheet deletes the token. Guests can't link accounts, and none of these
tools is lent to guests.

## Spotify

What Bella gets: what's playing, recent plays, top tracks and artists,
playlists and search. Play, pause, skip, queue and volume need Spotify Premium
and your yes each time.

1. Go to developer.spotify.com, open the Dashboard and create an app.
2. Add these redirect URIs, which must match exactly:
   - `http://127.0.0.1:8420/oauth/link/spotify/callback` (the shared ARC on the desktop)
   - `http://127.0.0.1:8421/oauth/link/spotify/callback` (private Bella)
   - `<ARC_PUBLIC_URL>/oauth/link/spotify/callback`, if you want to link from the phone
3. Copy the app's **Client ID** into `.env` as `SPOTIFY_CLIENT_ID=`. There is no secret: ARC uses PKCE.

Scopes asked for: `user-read-playback-state`, `user-read-currently-playing`,
`user-read-recently-played`, `user-top-read`, `playlist-read-private`,
`user-library-read`, and `user-modify-playback-state` (play and pause only).

A sign-in started through the tunnel is refused unless `ARC_PUBLIC_URL` is
set. The callback address is never guessed from a request header.

## Microsoft: Outlook and OneDrive

What Bella gets: Outlook mail, **read-only** (the same rule as Gmail: never
send, reply, move or delete), the Outlook calendar, read-only, and OneDrive
file search and reading.

1. In the Azure portal, open App registrations and create a new registration.
   For "Accounts in any organizational directory and personal Microsoft
   accounts", no redirect URI is needed.
2. Under Authentication, set **Allow public client flows** to Yes.
3. Copy the **Application (client) ID** into `.env` as `MS_CLIENT_ID=`.

Linking shows a short code. Enter it at microsoft.com/devicelogin, on any
device.

Scopes asked for: `offline_access User.Read Mail.Read Calendars.Read Files.Read`.
Not `Mail.ReadWrite` and not `Mail.Send`.

## GitHub

What Bella gets: your notifications, your repositories, public search, a
repository's README and its issues. Read-only.

1. On GitHub, go to Settings, Developer settings, OAuth Apps, and create a new OAuth App.
2. Tick **Enable Device Flow**. The callback URL field can be your ARC address; it isn't used.
3. Copy the **Client ID** into `.env` as `GITHUB_CLIENT_ID=`.

Scopes asked for: `read:user notifications`. GitHub has no read-only scope
for private repositories (`repo` can push), so private code stays out until
you decide otherwise.

## Notion

What Bella gets: search, and reading the pages and databases you share with
the integration. Nothing else in your workspace.

1. At notion.so/my-integrations, create an internal integration with read content only.
2. In Notion, open each page Bella may read, choose ••• then Connections, and add the integration.
3. Put the integration secret in `.env` as `NOTION_TOKEN=`.

This one belongs to the owner only.

## Asked for, and not possible

These appear in the sheet with the reason, so nobody has to guess:

- **Instagram**: the API for personal accounts closed in December 2024. Only
  Business and Creator accounts can be reached, through an app Meta reviews,
  and never your DMs.
- **Snapchat**: no API reads your snaps, chats or stories.
- **WhatsApp**: an API exists only for business numbers.
- **TikTok**: only an approved app can use the API, and it gives your own profile and videos.
- **iMessage**: Apple offers no API, and it can't be reached from Windows.
- **Facebook Messenger**: the API is for businesses answering customers.

What Bella can already do without any of these: open Spotify, YouTube or
Instagram in the browser (`open_website`), and read the screen when you ask.
