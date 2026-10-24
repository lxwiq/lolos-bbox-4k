package fr.accueil.tv;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Outline;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewOutlineProvider;
import android.view.animation.DecelerateInterpolator;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.TextView;

/** Tuile façon tvOS : coins arrondis, zoom + ombre + reflet au focus, nom affiché dessous. */
final class TileView extends FrameLayout {
    static final float FOCUS_SCALE = 1.12f;

    final Apps.Entry entry;
    final TextView label;
    final int row;    // -1 = dock, sinon ligne de la grille
    final int index;  // position dans le dock ou la grille

    private final View sheen;
    private final float density;
    private ObjectAnimator wiggle;

    TileView(Context context, Apps.Entry entry, TextView label, int row, int index) {
        super(context);
        this.entry = entry;
        this.label = label;
        this.row = row;
        this.index = index;
        this.density = getResources().getDisplayMetrics().density;

        setFocusable(true);
        setClickable(true);
        setDefaultFocusHighlightEnabled(false);
        final float radius = 10 * density;
        setOutlineProvider(new ViewOutlineProvider() {
            @Override
            public void getOutline(View view, Outline outline) {
                outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(), radius);
            }
        });
        setClipToOutline(true);
        setElevation(3 * density);

        if (entry.banner != null) {
            ImageView art = new ImageView(context);
            art.setScaleType(ImageView.ScaleType.CENTER_CROP);
            art.setImageDrawable(entry.banner);
            addView(art, new LayoutParams(-1, -1));
        } else {
            setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR, entry.colors));
            if (entry.icon != null) {
                ImageView art = new ImageView(context);
                art.setScaleType(ImageView.ScaleType.FIT_CENTER);
                int pad = (int) (18 * density);
                art.setPadding(pad, pad, pad, pad);
                art.setImageDrawable(entry.icon);
                addView(art, new LayoutParams(-1, -1));
            } else {
                TextView title = new TextView(context);
                title.setText(entry.label);
                title.setTextColor(0xFFFFFFFF);
                title.setTextSize(entry.label.length() > 10 ? 15 : 22);
                title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
                title.setGravity(Gravity.CENTER);
                title.setMaxLines(2);
                title.setEllipsize(TextUtils.TruncateAt.END);
                addView(title, new LayoutParams(-1, -1));
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

    @Override
    protected void onFocusChanged(boolean focused, int direction, android.graphics.Rect previous) {
        super.onFocusChanged(focused, direction, previous);
        float scale = focused ? FOCUS_SCALE : 1f;
        animate().scaleX(scale).scaleY(scale)
                .translationZ(focused ? 14 * density : 0)
                .setDuration(focused ? 180 : 140)
                .setInterpolator(new DecelerateInterpolator())
                .start();
        sheen.animate().alpha(focused ? 1f : 0f).setDuration(200).start();
        label.animate().alpha(focused ? 1f : 0f).translationY(focused ? 0 : -4 * density)
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
