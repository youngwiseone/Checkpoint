from pathlib import Path
p=Path('backend/checkpoint/desktop/cards.py')
s=p.read_text(encoding='utf-8')
a=s.index('        front_clip = QPainterPath()')
b=s.index('        painter.save()\n        # A diffuse',a)
s=s[:a]+'''        for back, transform, item, image in self.back_cards():
            painter.save()
            painter.setTransform(transform, combine=True)
            self._paint_card(painter, back, item, image, c, back=True)
            painter.restore()
        self._paint_card(painter, rect, self.item, self.image, c)
        if self.press is not None and abs(self.offset) > 16 and self.item:
            decision = 'APPROVE' if self.offset < 0 else 'DELETE'
            tint = c.success if self.offset < 0 else c.danger
            painter.setPen(QPen(tint, 2))
            painter.setBrush(colors.alpha(c.card, 240))
            badge = QRectF(rect.center().x() - 68, rect.top() + 12, 136, 38)
            painter.drawRoundedRect(badge, 12, 12)
            painter.setFont(QFont('Segoe UI', 12, QFont.Weight.Bold))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, decision)
        painter.end()

    def _paint_card(self, painter, rect, item, image, c, back=False):
'''+s[b:]
start=s.index('    def _paint_card(')
end=s.index('    def enterEvent(',start)
part=s[start:end]
part=part.replace('self.item','item').replace('self.image','image')
part=part.replace('        painter.end()\n','')
part=part.replace("'Swipe up or down · Click to expand' if item", "'↑ Approve · ↓ Delete · Click to expand' if item")
part=part.replace('strength=1.03 if self.hover else 1','strength=1.03 if self.hover and not back else 1')
s=s[:start]+part+s[end:]
s=s.replace("        if abs(delta.y()) >= 48 and abs(delta.y()) > abs(delta.x()):\n            self.navigate.emit(1 if delta.y() < 0 else -1)", "        if self.item and abs(delta.y()) >= self.swipe_threshold and abs(delta.y()) > abs(delta.x()):\n            self.decide.emit('done' if delta.y() < 0 else 'deleted')")
s=s.replace('        self.update()\n\n    def settle', '        self.update()\n        self._update_mask()\n\n    def _update_mask(self):\n        callback = getattr(self.window(), "update_glass_mask", None)\n        if callback:\n            callback()\n\n    def settle',1)
s=s.replace('        self.update()\n\n    def end_drag', '        self.update()\n        self._update_mask()\n\n    def end_drag',1)
p.write_text(s,encoding='utf-8')
