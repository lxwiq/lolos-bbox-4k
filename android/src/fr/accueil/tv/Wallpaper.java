package fr.accueil.tv;

import android.animation.AnimatorSet;
import android.animation.ObjectAnimator;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.animation.LinearInterpolator;
import android.widget.ImageView;

import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.function.Predicate;

/**
 * Fond animé : vidéos (.mp4) en boucle sur une SurfaceView (plan vidéo matériel) et photos
 * (.jpg/.png) animées par un lent zoom, depuis /sdcard/Android/data/fr.accueil.tv/files/fonds/.
 * Un « rideau » noir au-dessus sert aux fondus. Le lecteur est libéré dès que l'écran
 * n'est plus visible, pour rendre le décodeur à la TV.
 */
final class Wallpaper implements SurfaceHolder.Callback {
    private static final long DUREE_PHOTO_MS = 25_000;

    private final Context context;
    private final View curtain;
    private final ImageView photo;
    private final SharedPreferences prefs;
    private final String indexKey;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable photoSuivante = this::next;

    private SurfaceHolder holder;
    private MediaPlayer player;
    private AnimatorSet zoom;
    private Predicate<File> filtre = f -> true;
    private boolean wanted;
    private boolean paused;
    private boolean prepared;
    private boolean affichePhoto;
    private int failures;

    Wallpaper(Context context, SurfaceView surface, ImageView photo, View curtain, String indexKey) {
        this.context = context;
        this.curtain = curtain;
        this.photo = photo;
        this.indexKey = indexKey;
        this.prefs = context.getSharedPreferences("fond", Context.MODE_PRIVATE);
        photo.setScaleType(ImageView.ScaleType.CENTER_CROP);
        photo.setVisibility(View.GONE);
        surface.getHolder().addCallback(this);
    }

    static File folder(Context context) {
        return context.getExternalFilesDir("fonds");
    }

    static boolean estPhoto(File f) {
        String n = f.getName().toLowerCase(Locale.ROOT);
        return n.endsWith(".jpg") || n.endsWith(".jpeg") || n.endsWith(".png");
    }

    /** Fonds à montrer (paysages Lol'OS, vos fonds, ceux du mode nuit…). */
    void setFiltre(Predicate<File> filtre) {
        this.filtre = filtre;
    }

    private List<File> files() {
        File dir = folder(context);
        File[] found = dir == null ? null : dir.listFiles(f -> f.getName().toLowerCase(Locale.ROOT).endsWith(".mp4") || estPhoto(f));
        if (found == null) return Collections.emptyList();
        Arrays.sort(found);
        List<File> choisis = new ArrayList<>();
        for (File f : found) if (filtre.test(f)) choisis.add(f);
        // Filtre trop strict (ex. aucun fond calme) : on garde tout plutôt qu'un écran vide
        return choisis.isEmpty() ? Arrays.asList(found) : choisis;
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

    /** Fondu au noir, puis fond suivant. */
    void next() {
        if (!wanted || (player == null && !affichePhoto)) return;
        handler.removeCallbacks(photoSuivante);
        curtain.animate().alpha(1f).setDuration(700).withEndAction(() -> {
            release();
            bump();
            play();
        });
    }

    void setPaused(boolean pause) {
        if (pause == paused) return;
        paused = pause;
        if (affichePhoto && zoom != null) {
            if (pause) zoom.pause();
            else zoom.resume();
        }
        if (player == null || !prepared) return;
        if (pause) player.pause();
        else player.start();
    }

    private void bump() {
        int n = files().size();
        if (n > 0) prefs.edit().putInt(indexKey, (prefs.getInt(indexKey, -1) + 1) % n).apply();
    }

    private void play() {
        if (!wanted || holder == null || player != null || affichePhoto) return;
        List<File> files = files();
        if (files.isEmpty()) {
            showFallback();
            return;
        }
        File file = files.get(Math.max(0, prefs.getInt(indexKey, 0)) % files.size());
        if (estPhoto(file)) {
            montrerPhoto(file);
            return;
        }
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

    /** Photo plein écran, zoom lent de 25 s (effet « Ken Burns »), puis la suivante. */
    private void montrerPhoto(File file) {
        BitmapFactory.Options taille = new BitmapFactory.Options();
        taille.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(file.getPath(), taille);
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = 1;
        while (taille.outWidth / (options.inSampleSize * 2) >= 1920) options.inSampleSize *= 2;
        Bitmap image = BitmapFactory.decodeFile(file.getPath(), options);
        if (image == null) {
            onFailure();
            return;
        }
        affichePhoto = true;
        photo.setImageBitmap(image);
        photo.setScaleX(1f);
        photo.setScaleY(1f);
        photo.setVisibility(View.VISIBLE);
        zoom = new AnimatorSet();
        zoom.playTogether(
                ObjectAnimator.ofFloat(photo, View.SCALE_X, 1f, 1.12f),
                ObjectAnimator.ofFloat(photo, View.SCALE_Y, 1f, 1.12f),
                ObjectAnimator.ofFloat(photo, View.TRANSLATION_X, 0f, -40f));
        zoom.setDuration(DUREE_PHOTO_MS + 2000);
        zoom.setInterpolator(new LinearInterpolator());
        zoom.start();
        if (paused) zoom.pause();
        failures = 0;
        curtain.animate().alpha(0f).setDuration(900);
        handler.postDelayed(photoSuivante, DUREE_PHOTO_MS);
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

    /** Pas de fond lisible : dégradé fixe. */
    private void showFallback() {
        curtain.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] { 0xFF1E4A66, 0xFF0D2233, 0xFF06121C }));
        curtain.setAlpha(1f);
    }

    private void release() {
        handler.removeCallbacks(photoSuivante);
        if (affichePhoto) {
            affichePhoto = false;
            if (zoom != null) zoom.cancel();
            zoom = null;
            photo.setVisibility(View.GONE);
            photo.setImageDrawable(null);
        }
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
