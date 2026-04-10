document.addEventListener('DOMContentLoaded', () => {

    /* ---- Scroll-triggered fade-ins ---- */
    const fadeEls = document.querySelectorAll('.fade-in');
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1 });
    fadeEls.forEach(el => observer.observe(el));

    /* ---- Card glow follows data-color attr ---- */
    document.querySelectorAll('.app-card').forEach(card => {
        const rgb = card.dataset.color;
        if (rgb) {
            card.style.setProperty('--glow-rgb', rgb);
        }
    });

    /* ---- Obfuscated contact email ---- */
    const contactLink = document.getElementById('contact-link');
    if (contactLink) {
        const user = 'contact';
        const domain = 'finndigital.net';
        contactLink.addEventListener('click', (e) => {
            e.preventDefault();
            window.location.href = 'mailto:' + user + '@' + domain;
        });
    }

    /* ---- Hero parallax on mouse move ---- */
    const hero = document.querySelector('.hero');
    const circles = document.querySelector('.hero-circles');
    const heroContent = document.querySelector('.hero-content');

    if (hero && circles && heroContent) {
        hero.addEventListener('mousemove', (e) => {
            const rect = hero.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width - 0.5;
            const y = (e.clientY - rect.top) / rect.height - 0.5;

            circles.style.transform = `translate(calc(-50% + ${x * 20}px), calc(-50% + ${y * 20}px))`;
            heroContent.style.transform = `translate(${x * -8}px, ${y * -8}px)`;
        });

        hero.addEventListener('mouseleave', () => {
            circles.style.transform = 'translate(-50%, -50%)';
            circles.style.transition = 'transform 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
            heroContent.style.transform = 'translate(0, 0)';
            heroContent.style.transition = 'transform 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
            setTimeout(() => {
                circles.style.transition = '';
                heroContent.style.transition = '';
            }, 600);
        });
    }

    /* ---- Promo Modal ---- */
    const promoModal = document.getElementById('promo-modal');
    const promoOpen = document.getElementById('promo-open');
    const promoClose = document.getElementById('promo-close');
    const promoMusic = document.getElementById('promo-music');
    const promoProgress = document.getElementById('promo-progress');

    if (promoModal && promoOpen) {
        const promoSlides = promoModal.querySelectorAll('.promo-slide');
        const promoTimings = [3500, 3500, 4000, 3500, 5000];
        let promoIdx = 0;
        let promoTimer = null;

        function resetPromoAnimations(slide) {
            slide.querySelectorAll('*').forEach(el => {
                const s = getComputedStyle(el);
                if (s.animationName && s.animationName !== 'none') {
                    el.style.animation = 'none';
                    el.offsetHeight;
                    el.style.animation = '';
                }
            });
        }

        function showPromoSlide(idx) {
            promoSlides.forEach(s => s.classList.remove('promo-active'));
            resetPromoAnimations(promoSlides[idx]);
            promoSlides[idx].classList.add('promo-active');
            promoProgress.style.width = ((idx + 1) / promoSlides.length * 100) + '%';
        }

        function nextPromoSlide() {
            promoIdx++;
            if (promoIdx < promoSlides.length) {
                showPromoSlide(promoIdx);
                promoTimer = setTimeout(nextPromoSlide, promoTimings[promoIdx]);
            } else {
                let vol = promoMusic.volume;
                const fade = setInterval(() => {
                    vol -= 0.05;
                    if (vol <= 0) {
                        promoMusic.volume = 0;
                        promoMusic.pause();
                        clearInterval(fade);
                    } else {
                        promoMusic.volume = vol;
                    }
                }, 100);
            }
        }

        function startPromo() {
            promoIdx = 0;
            promoModal.classList.add('open');
            document.body.style.overflow = 'hidden';
            showPromoSlide(0);
            promoMusic.volume = 0.6;
            promoMusic.currentTime = 0;
            promoMusic.play();
            promoTimer = setTimeout(nextPromoSlide, promoTimings[0]);
        }

        function stopPromo() {
            promoModal.classList.remove('open');
            document.body.style.overflow = '';
            promoMusic.pause();
            promoMusic.currentTime = 0;
            clearTimeout(promoTimer);
            promoProgress.style.width = '0';
        }

        promoOpen.addEventListener('click', startPromo);
        promoClose.addEventListener('click', stopPromo);
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && promoModal.classList.contains('open')) {
                stopPromo();
            }
        });
    }
});
