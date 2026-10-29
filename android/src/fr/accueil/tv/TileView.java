package fr.accueil.tv;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Outline;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewOutlineProvider;
import android.view.animation.DecelerateInterpolator;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Tuile façon tvOS : coins arrondis, zoom + ombre + reflet au focus, nom affiché dessous. */
final class TileView extends FrameLayout {
    static final float FOCUS_SCALE = 1.12f;

    final Apps.Entry entry;
    final TextView label;
    final int row;    // -1 = dock, sinon ligne de la grille
    final int index;  // position dans le dock ou la grille

    private final View sheen;
    private final float unite;  // pixels par unité d'écran (1/960 de la largeur)
    private ObjectAnimator wiggle;

    TileView(Context context, Apps.Entry entry, TextView label, int row, int index, float unite) {
        super(context);
        this.entry = entry;
        this.label = label;
        this.row = row;
        this.index = index;
        this.unite = unite;

        setFocusable(true);
        setClickable(true);
        setDefaultFocusHighlightEnabled(false);
        final float radius = 8 * unite;
        setOutlineProvider(new ViewOutlineProvider() {
            @Override
            public void getOutline(View view, Outline outline) {
                outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(), radius);
            }
        });
        setClipToOutline(true);
        setElevation(3 * unite);

        if (entry.banner != null) {
            ImageView art = new ImageView(context);
            art.setScaleType(ImageView.ScaleType.CENTER_CROP);
            art.setImageDrawable(entry.banner);
            addView(art, new LayoutParams(-1, -1));
        } else {
            setBackgroundColor(entry.color);
            if (entry.icon != null) {
                ImageView art = new ImageView(context);
                art.setScaleType(ImageView.ScaleType.FIT_CENTER);
                int pad = Math.round(20 * unite);
                art.setPadding(pad, pad, pad, pad);
                art.setImageDrawable(entry.icon);
                addView(art, new LayoutParams(-1, -1));
            } else {
                addView(titre(context), new LayoutParams(-1, -1));
            }
        }

        sheen = new View(context);
        sheen.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[] { 0x40FFFFFF, 0x0DFFFFFF, 0x00FFFFFF }));
        sheen.setAlpha(0f);
        addView(sheen, new LayoutParams(-1, -1));

        label.setText(entry.label);
        label.setAlpha(0f);
    }

    /** Tuile sans image (TV, Programme TV, Réglages…) : pictogramme et nom, centrés. */
    private View titre(Context context) {
        LinearLayout box = new LinearLayout(context);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        int marge = Math.round(10 * unite);
        box.setPadding(marge, marge, marge, marge);
        if (Glyphe.existe(entry.key)) {
            View glyphe = new View(context);
            glyphe.setBackground(new Glyphe(entry.key));
            int taille = Math.round(26 * unite);
            LinearLayout.LayoutParams gl = new LinearLayout.LayoutParams(taille, taille);
            gl.bottomMargin = Math.round(7 * unite);
            box.addView(glyphe, gl);
        }
        TextView title = new TextView(context);
        title.setText(entry.label);
        title.setTextColor(0xFFFFFFFF);
        title.setTextSize(TypedValue.COMPLEX_UNIT_PX, 14 * unite);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        title.setGravity(Gravity.CENTER);
        title.setMaxLines(2);
        title.setEllipsize(TextUtils.TruncateAt.END);
        box.addView(title, new LinearLayout.LayoutParams(-2, -2));
        return box;
    }

    @Override
    protected void onFocusChanged(boolean focused, int direction, android.graphics.Rect previous) {
        super.onFocusChanged(focused, direction, previous);
        float scale = focused ? FOCUS_SCALE : 1f;
        animate().scaleX(scale).scaleY(scale)
                .translationZ(focused ? 14 * unite : 0)
                .setDuration(focused ? 180 : 140)
                .setInterpolator(new DecelerateInterpolator())
                .start();
        sheen.animate().alpha(focused ? 1f : 0f).setDuration(200).start();
        label.animate().alpha(focused ? 1f : 0f).translationY(focused ? 0 : -4 * unite)
                .setDuration(180).start();
    }

    /** Mode déplacement : la tuile tremblote comme sur Apple TV. */
    void setWiggling(boolean on) {
        if (on && wiggle == null) {
            wiggle = ObjectAnimator.ofFloat(this, "rotation", -1.5f, 1.5f);
            wiggle.setDuration(120);
            wiggle.setRepeatCount(ValueAnimator.INFINITE);
            wiggle.setRepeatMode(ValueAnimator.REVERSE);
            wiggle.start();
        } else if (!on && wiggle != null) {
            wiggle.cancel();
            wiggle = null;
            setRotation(0f);
        }
    }
}
