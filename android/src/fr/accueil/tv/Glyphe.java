package fr.accueil.tv;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/**
 * Pictogrammes des tuiles spéciales, dessinés au trait blanc sur une grille de 24 × 24
 * (aucune image à embarquer, nets à toutes les résolutions).
 */
final class Glyphe extends Drawable {
    private final String key;
    private final Paint trait = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint plein = new Paint(Paint.ANTI_ALIAS_FLAG);

    Glyphe(String key) {
        this.key = key;
        trait.setStyle(Paint.Style.STROKE);
        trait.setStrokeWidth(1.8f);
        trait.setStrokeCap(Paint.Cap.ROUND);
        trait.setStrokeJoin(Paint.Join.ROUND);
        trait.setColor(0xFFFFFFFF);
        plein.setStyle(Paint.Style.FILL);
        plein.setColor(0xFFFFFFFF);
    }

    static boolean existe(String key) {
        switch (key) {
            case "@tv":
            case "@guide":
            case "@records":
            case "@settings":
            case "@reboot":
            case "@veille":
            case "@infos":
                return true;
            default:
                return false;
        }
    }

    @Override
    public void draw(Canvas c) {
        Rect b = getBounds();
        float s = Math.min(b.width(), b.height());
        c.save();
        c.translate(b.exactCenterX() - s / 2, b.exactCenterY() - s / 2);
        c.scale(s / 24f, s / 24f);
        switch (key) {
            case "@tv":  // écran sur pied
                c.drawRoundRect(new RectF(2.5f, 4f, 21.5f, 16.5f), 2f, 2f, trait);
                c.drawLine(8f, 20.5f, 16f, 20.5f, trait);
                break;
            case "@guide":  // grille des programmes
                c.drawRoundRect(new RectF(3f, 4f, 21f, 20f), 2f, 2f, trait);
                c.drawLine(3f, 9f, 21f, 9f, trait);
                c.drawLine(8.5f, 9f, 8.5f, 20f, trait);
                c.drawLine(12f, 13f, 17.5f, 13f, trait);
                c.drawLine(12f, 16.5f, 15.5f, 16.5f, trait);
                break;
            case "@records":  // bouton d'enregistrement
                c.drawCircle(12f, 12f, 9f, trait);
                c.drawCircle(12f, 12f, 4.5f, plein);
                break;
            case "@settings":  // roue crantée
                Paint dent = new Paint(trait);
                dent.setStrokeWidth(3.4f);
                dent.setStrokeCap(Paint.Cap.BUTT);
                for (int i = 0; i < 8; i++) {
                    double a = Math.toRadians(i * 45 + 22.5);
                    c.drawLine(12f + 6.5f * (float) Math.cos(a), 12f + 6.5f * (float) Math.sin(a),
                            12f + 10f * (float) Math.cos(a), 12f + 10f * (float) Math.sin(a), dent);
                }
                c.drawCircle(12f, 12f, 6.8f, trait);
                c.drawCircle(12f, 12f, 2.6f, trait);
                break;
            case "@reboot": {  // flèche circulaire
                float r = 7.5f, fin = 260f;
                c.drawArc(new RectF(12f - r, 12f - r, 12f + r, 12f + r), fin - 300f, 300f, false, trait);
                double a = Math.toRadians(fin);
                float ex = 12f + r * (float) Math.cos(a), ey = 12f + r * (float) Math.sin(a);
                float dx = (float) -Math.sin(a), dy = (float) Math.cos(a);  // sens de parcours
                Path pointe = new Path();
                pointe.moveTo(ex + 3.2f * dx, ey + 3.2f * dy);
                pointe.lineTo(ex - 0.6f * dx + 2.8f * dy, ey - 0.6f * dy - 2.8f * dx);
                pointe.lineTo(ex - 0.6f * dx - 2.8f * dy, ey - 0.6f * dy + 2.8f * dx);
                pointe.close();
                c.drawPath(pointe, plein);
                break;
            }
            case "@veille": {  // croissant de lune
                Path lune = new Path();
                lune.addCircle(12f, 12.5f, 8.5f, Path.Direction.CW);
                Path ombre = new Path();
                ombre.addCircle(16.5f, 8.5f, 7f, Path.Direction.CW);
                lune.op(ombre, Path.Op.DIFFERENCE);
                c.drawPath(lune, trait);
                break;
            }
            case "@infos":  // i cerclé
                c.drawCircle(12f, 12f, 9f, trait);
                c.drawCircle(12f, 7.8f, 1.3f, plein);
                c.drawLine(12f, 11f, 12f, 16.5f, trait);
                break;
        }
        c.restore();
    }

    @Override
    public void setAlpha(int alpha) {
        trait.setAlpha(alpha);
        plein.setAlpha(alpha);
    }

    @Override
    public void setColorFilter(ColorFilter filter) {
        trait.setColorFilter(filter);
        plein.setColorFilter(filter);
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSLUCENT;
    }
}
