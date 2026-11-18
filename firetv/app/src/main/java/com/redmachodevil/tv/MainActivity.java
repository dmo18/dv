package com.redmachodevil.tv;

import android.app.Activity;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.net.Uri;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Red Macho Devil TV for Fire TV: a full-screen WebView that opens the live TV page.
 * The page itself is on GitHub Pages, so every change to it shows up here with no reinstall.
 * The remote: Up and Down change channel, Left and Right move between the buttons, Select presses a button,
 * Menu opens the guide, Play/Pause mutes, Back closes the guide (and leaves the app when it is closed).
 */
public class MainActivity extends Activity {
    private static final String HOME = "https://dmo18.github.io/dv/tv.html";
    private static final String HOST = "dmo18.github.io";
    private WebView web;
    private MediaSession session;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        web = new WebView(this);
        web.setBackgroundColor(0xFF000000);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);   // sound without a tap
        s.setCacheMode(WebSettings.LOAD_DEFAULT);

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // stay on the TV site; do not let the page send the whole screen somewhere else
                return !HOST.equals(Uri.parse(url).getHost());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // press "Tap to rock" for the viewer
                view.evaluateJavascript(
                    "setTimeout(function(){var s=document.getElementById('start');if(s&&getComputedStyle(s).display!=='none'){s.click();}},800);",
                    null);
            }
        });
        web.loadUrl(HOME);
        web.requestFocus();
        startMediaSession();
    }

    /** Media keys (Play/Pause and so on) only reach an app that has a media session, so register one. */
    private void startMediaSession() {
        session = new MediaSession(this, "RedMachoDevilTV");
        session.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { sendKey("m"); }
            @Override public void onPause() { sendKey("m"); }
            @Override public void onSkipToNext() { sendKey("ArrowUp"); }
            @Override public void onSkipToPrevious() { sendKey("ArrowDown"); }
        });
        session.setPlaybackState(new PlaybackState.Builder()
            .setActions(PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_PLAY_PAUSE
                | PlaybackState.ACTION_SKIP_TO_NEXT | PlaybackState.ACTION_SKIP_TO_PREVIOUS)
            .setState(PlaybackState.STATE_PLAYING, 0, 1f)
            .build());
        session.setActive(true);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        }
    }

    private void sendKey(String key) {
        web.evaluateJavascript(
            "document.dispatchEvent(new KeyboardEvent('keydown',{key:'" + key + "',bubbles:true}));", null);
    }

    @Override
    public boolean onKeyDown(int code, KeyEvent event) {
        switch (code) {
            case KeyEvent.KEYCODE_MENU:
                sendKey("g");
                return true;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_MEDIA_PLAY:
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                sendKey("m");
                return true;
            case KeyEvent.KEYCODE_CHANNEL_UP:
            case KeyEvent.KEYCODE_PAGE_UP:
                sendKey("ArrowUp");
                return true;
            case KeyEvent.KEYCODE_CHANNEL_DOWN:
            case KeyEvent.KEYCODE_PAGE_DOWN:
                sendKey("ArrowDown");
                return true;
            case KeyEvent.KEYCODE_BACK:
                // close the guide if it is open, otherwise leave the app
                web.evaluateJavascript(
                    "(function(){var g=document.getElementById('guide');return !!g&&g.classList.contains('open');})()",
                    new ValueCallback<String>() {
                        @Override
                        public void onReceiveValue(String open) {
                            if ("true".equals(open)) {
                                sendKey("Escape");
                            } else {
                                finish();
                            }
                        }
                    });
                return true;
            default:
                return super.onKeyDown(code, event);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        web.onPause();
        web.pauseTimers();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        web.resumeTimers();
    }

    @Override
    protected void onDestroy() {
        if (session != null) { session.setActive(false); session.release(); }
        web.destroy();
        super.onDestroy();
    }
}
