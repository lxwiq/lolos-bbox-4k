package fr.accueil.tv;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;

import java.io.File;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Fond animé : lit en boucle les vidéos de
 * /sdcard/Android/data/fr.accueil.tv/files/fonds/*.mp4 sur une SurfaceView (plan vidéo matériel).
 * Un « rideau » noir au-dessus sert aux fondus. Le lecteur est libéré dès que l'écran
 * n'est plus visible, pour rendre le décodeur à la TV.
 */
final class Wallpaper implements SurfaceHolder.Callback {
    private final Context context;
    private final View curtain;
    private final SharedPreferences prefs;
    private final String indexKey;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private SurfaceHolder holder;
    private MediaPlayer player;
    private boolean wanted;
    private boolean paused;
    private boolean prepared;
    private int failures;

    Wallpaper(Context context, SurfaceView surface, View curtain, String indexKey) {
        this.context = context;
        this.curtain = curtain;
        this.indexKey = indexKey;
        this.prefs = context.getSharedPreferences("fond", Context.MODE_PRIVATE);
        surface.getHolder().addCallback(this);
    }

    static File folder(Context context) {
        return context.getExternalFilesDir("fonds");
    }

    private List<File> files() {
        File dir = folder(context);
        File[] found = dir == null ? null : dir.listFiles(f -> f.getName().endsWith(".mp4"));
        if (found == null) return Collections.emptyList();
        Arrays.sort(found);
        return Arrays.asList(found);
    }

    void start(boolean advance) {
        wanted = true;
        paused = false;
        failures = 0;
        if (advance) bump();
        play();
    }

    void stop() {
        wanted = false;
        handler.removeCallbacksAndMessages(null);
        release();
        curtain.animate().cancel();
        curtain.setAlpha(1f);
    }

    /** Fondu au noir, puis paysage suivant. */
    void next() {
        if (!wanted || player == null) return;
        curtain.animate().alpha(1f).setDuration(700).withEndAction(() -> {
            release();
            bump();
            play();
        });
    }

    void setPaused(boolean pause) {
        if (pause == paused) return;
        paused = pause;
        if (player == null || !prepared) return;
        if (pause) player.pause();
        else player.start();
    }

    private void bump() {
        int n = files().size();
        if (n > 0) prefs.edit().putInt(indexKey, (prefs.getInt(indexKey, -1) + 1) % n).apply();
    }

    private void play() {
        if (!wanted || holder == null || player != null) return;
        List<File> files = files();
        if (files.isEmpty()) {
            showFallback();
            return;
        }
        File file = files.get(Math.max(0, prefs.getInt(indexKey, 0)) % files.size());
        MediaPlayer mp = new MediaPlayer();
        player = mp;
        prepared = false;
        try {
            mp.setDataSource(file.getPath());
            mp.setDisplay(holder);
            mp.setLooping(true);
            mp.setVolume(0f, 0f);
            mp.setOnPreparedListener(p -> {
                prepared = true;
                p.setVideoScalingMode(MediaPlayer.VIDEO_SCALING_MODE_SCALE_TO_FIT_WITH_CROPPING);
                if (!paused) p.start();
            });
            mp.setOnInfoListener((p, what, extra) -> {
                if (what == MediaPlayer.MEDIA_INFO_VIDEO_RENDERING_START) {
                    failures = 0;
                    curtain.animate().alpha(0f).setDuration(900);
                }
                return false;
            });
            mp.setOnErrorListener((p, what, extra) -> {
                onFailure();
                return true;
            });
            mp.prepareAsync();
        } catch (Exception e) {
            onFailure();
        }
    }

    /** Le décodeur peut être encore tenu par l'appli qu'on vient de quitter : on réessaie. */
    private void onFailure() {
        release();
        if (++failures <= Math.max(3, files().size())) {
            bump();
            handler.postDelayed(this::play, 600L * failures);
        } else {
            showFallback();
        }
    }

    /** Pas de vidéo lisible : dégradé fixe. */
    private void showFallback() {
        curtain.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] { 0xFF1E4A66, 0xFF0D2233, 0xFF06121C }));
        curtain.setAlpha(1f);
    }

    private void release() {
        if (player == null) return;
        player.setOnInfoListener(null);
        player.setOnErrorListener(null);
        player.release();
        player = null;
        prepared = false;
    }

    @Override
    public void surfaceCreated(SurfaceHolder h) {
        holder = h;
        play();
    }

    @Override
    public void surfaceChanged(SurfaceHolder h, int format, int width, int height) {
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder h) {
        holder = null;
        release();
    }
}
